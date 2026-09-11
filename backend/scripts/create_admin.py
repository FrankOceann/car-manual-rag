"""Create the first administrator; credentials are never accepted on the command line."""
import argparse
from getpass import getpass
from sqlalchemy import select
from app.auth import hash_password
from app.db import session_factory
from app.models import User


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--username", default="admin")
    args = parser.parse_args()
    if not args.username.strip() or len(args.username) > 80:
        parser.error("用户名需要 1–80 字符。")
    password = getpass("管理员密码（至少 10 字符）: ")
    if len(password) < 10 or len(password) > 256 or password != getpass("再次输入密码: "):
        parser.error("密码长度不合要求或两次不一致。")
    with session_factory()() as session:
        if session.scalar(select(User).where(User.username == args.username.strip())):
            parser.error("该用户名已存在；没有修改现有账户。")
        session.add(User(username=args.username.strip(), password_hash=hash_password(password), role="admin"))
        session.commit()
    print("管理员已创建。")


if __name__ == "__main__":
    main()
