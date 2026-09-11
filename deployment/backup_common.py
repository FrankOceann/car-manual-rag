from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_VOLUMES = ("chroma", "manuals", "models", "redis", "beat")
WRITERS = ("nginx", "api", "beat", "worker")


def run(*args: str, capture: bool = False) -> str:
    result = subprocess.run(args, cwd=ROOT, check=True, text=True, encoding="utf-8",
                            stdout=subprocess.PIPE if capture else None)
    return result.stdout.strip() if capture else ""


def compose(project: str, *args: str, capture: bool = False) -> str:
    return run("docker", "compose", "--project-name", project,
               "--file", str(ROOT / "compose.yaml"), *args, capture=capture)


def config(project: str) -> dict:
    return json.loads(compose(project, "config", "--format", "json", capture=True))


def digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            result.update(block)
    return result.hexdigest()


def archive_volume(image: str, volume: str, folder: Path, filename: str, restore: bool) -> None:
    # Only mount the selected volume and backup folder, never the Docker socket or repo.
    code = (
        "import tarfile,pathlib; p=pathlib.Path('/volume').resolve(); "
        "assert not any(p.iterdir()), 'Target volume is not empty'; "
        "t=tarfile.open('/backup/' + __import__('sys').argv[1], 'r:gz'); members=t.getmembers(); "
        "[(lambda q: (q == p or p in q.parents) and (m.isdir() or m.isreg()) or (_ for _ in ()).throw(ValueError('Unsafe archive member')))((p / m.name).resolve()) for m in members]; "
        "t.extractall(p, members=members, numeric_owner=True); t.close()"
        if restore else
        "import tarfile,pathlib,sys; p=pathlib.Path('/volume').resolve(); "
        "t=tarfile.open('/backup/' + sys.argv[1], 'w:gz'); paths=[p,*p.rglob('*')]; "
        "[(lambda r,q: (q == p or p in q.parents) and (q.is_dir() or q.is_file()) or "
        "(_ for _ in ()).throw(ValueError('Unsafe archive source')))(r,r.resolve()) for r in paths]; "
        "[t.add(r.resolve(), arcname='.' if r == p else str(r.relative_to(p)), recursive=False) for r in paths]; "
        "t.close()"
    )
    run("docker", "run", "--rm", "--network", "none", "--user", "0",
        "--mount", f"type=volume,src={volume},dst=/volume" + ("" if restore else ",readonly"),
        "--mount", f"type=bind,src={folder},dst=/backup" + (",readonly" if restore else ""),
        "--entrypoint", "python", image, "-c", code, filename)


def backup(project: str, folder: Path) -> None:
    cfg = config(project)
    folder.mkdir(parents=True, exist_ok=False)
    running = set(compose(project, "ps", "--services", "--status", "running", capture=True).splitlines())
    if not {"postgres", "redis", "chroma"}.issubset(running):
        raise RuntimeError("Start postgres, redis and chroma before backing up.")
    stopped: list[str] = []
    try:
        # Stop incoming writes/schedules first; then let active worker tasks finish.
        for service in WRITERS:
            if service in running:
                stopped.append(service)
                compose(project, "stop", "--timeout", "1200", service)
        pg = cfg["services"]["postgres"]["environment"]
        compose(project, "exec", "-T", "postgres", "pg_dump", "-U", pg["POSTGRES_USER"],
                "-d", pg["POSTGRES_DB"], "--format=custom", "--file=/tmp/rag-backup.dump")
        compose(project, "cp", "postgres:/tmp/rag-backup.dump", str(folder / "postgres.dump"))
        compose(project, "exec", "-T", "postgres", "rm", "/tmp/rag-backup.dump")
        for service in ("chroma", "redis"):
            stopped.append(service)
            compose(project, "stop", "--timeout", "60", service)
        for key in DATA_VOLUMES:
            archive_volume(cfg["services"]["api"]["image"], cfg["volumes"][key]["name"],
                           folder, key + ".tar.gz", False)
        files = ["postgres.dump", *(key + ".tar.gz" for key in DATA_VOLUMES)]
        manifest = {"format": 1, "project": project,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "images": {k: v.get("image") for k, v in cfg["services"].items()},
                    "archive_image": run("docker", "image", "inspect", cfg["services"]["api"]["image"],
                                         "--format", "{{.Id}}", capture=True),
                    "sha256": {name: digest(folder / name) for name in files}}
        (folder / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        print(f"Backup completed: {folder}")
    finally:
        # Restart only services that were running before this script.
        if stopped:
            compose(project, "start", *reversed(stopped))


def restore(project: str, folder: Path) -> None:
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("format") != 1 or project == manifest.get("project"):
        raise RuntimeError("Use a new project name and a supported backup format.")
    required = {"postgres.dump", *(key + ".tar.gz" for key in DATA_VOLUMES)}
    if set(manifest.get("sha256", {})) != required:
        raise RuntimeError("Backup manifest has unexpected or missing files.")
    for name in required:
        if digest(folder / name) != manifest["sha256"][name]:
            raise RuntimeError(f"Backup checksum mismatch: {name}")
    cfg = config(project)
    existing = set(run("docker", "volume", "ls", "--format", "{{.Name}}", capture=True).splitlines())
    target_names = {v["name"] for v in cfg["volumes"].values()}
    if existing & target_names:
        raise RuntimeError("Target volumes already exist; choose a fresh --project. Nothing was overwritten.")
    if run("docker", "ps", "-aq", "--filter", f"label=com.docker.compose.project={project}", capture=True):
        raise RuntimeError("Target project already has containers; choose a fresh --project.")
    for service in ("postgres", "redis", "chroma"):
        if manifest["images"][service] != cfg["services"][service]["image"]:
            raise RuntimeError(f"Image version mismatch for {service}; restore with the original release.")
    # A tag can change; the archived immutable image ID must still be available locally.
    image = manifest["archive_image"]
    run("docker", "image", "inspect", image, "--format", "{{.Id}}", capture=True)
    for key, value in cfg["volumes"].items():
        run("docker", "volume", "create", "--label", f"com.docker.compose.project={project}",
            "--label", f"com.docker.compose.volume={key}", value["name"])
    for key in DATA_VOLUMES:
        archive_volume(image, cfg["volumes"][key]["name"], folder, key + ".tar.gz", True)
    compose(project, "up", "-d", "--wait", "postgres")
    compose(project, "cp", str(folder / "postgres.dump"), "postgres:/tmp/rag-restore.dump")
    pg = cfg["services"]["postgres"]["environment"]
    compose(project, "exec", "-T", "postgres", "pg_restore", "--exit-on-error", "--no-owner",
            "--no-privileges", "-U", pg["POSTGRES_USER"], "-d", pg["POSTGRES_DB"], "/tmp/rag-restore.dump")
    compose(project, "exec", "-T", "postgres", "rm", "/tmp/rag-restore.dump")
    compose(project, "up", "-d", "--wait", "redis", "chroma")
    print(f"Restored into {project}. Original volumes were preserved. "
          "Review the restored data, then build/start the application with this project name.")


def main(operation: str) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, help="Compose project (new name required for restore)")
    parser.add_argument("--" + ("output" if operation == "backup" else "backup"), required=True, type=Path)
    args = parser.parse_args()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", args.project):
        parser.error("Project must contain lowercase letters, digits, underscores or hyphens.")
    folder = (args.output if operation == "backup" else args.backup).resolve()
    try:
        (backup if operation == "backup" else restore)(args.project, folder)
    except (RuntimeError, OSError, ValueError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f"{operation} failed: {exc}\n")
