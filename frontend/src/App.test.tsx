import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import App from "./App";

describe("App", () => {
  afterEach(cleanup);

  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        if (String(input).endsWith("/vehicles")) {
          return Promise.resolve(
            new Response(JSON.stringify([{ id: "honda-civic", brand: "本田", model: "思域", year: 2024 }])),
          );
        }
        if (String(input).endsWith("/manuals/honda-civic/chapters")) {
          return Promise.resolve(
            new Response(JSON.stringify(["第 657 页", "第 724 页"])),
          );
        }
        return Promise.resolve(
          new Response(JSON.stringify({
            answer: "请按手册步骤检查机油。",
            steps: ["停在水平地面", "等待三分钟"],
            warnings: ["不要加注过量"],
            citations: [{
              manual_title: "2024 Honda Civic Owner's Manual",
              chapter_title: "机油检查",
              page_number: 657,
              excerpt: "Park the vehicle on level ground.",
            }],
            grounded: true,
          })),
        );
      }),
    );
  });

  it("requires a vehicle before asking and renders returned citations", async () => {
    const user = userEvent.setup();
    render(<App />);

    expect(await screen.findByLabelText("车型")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "开始查询" })).toBeDisabled();

    await user.selectOptions(screen.getByLabelText("车型"), "honda-civic");
    await user.type(screen.getByLabelText("问题"), "如何检查机油？");
    await user.click(screen.getByRole("button", { name: "开始查询" }));

    expect(await screen.findByText("机油检查 · PDF 第 657 页")).toBeInTheDocument();
    expect(screen.getByText("请按手册步骤检查机油。")).toBeInTheDocument();
  });

  it("loads vehicle chapters and lets an example question populate the input", async () => {
    const user = userEvent.setup();
    render(<App />);

    await user.selectOptions(await screen.findByLabelText("车型"), "honda-civic");
    expect(await screen.findByRole("checkbox", { name: "第 657 页" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "如何检查发动机机油液位？" }));
    expect(screen.getByLabelText("问题")).toHaveValue("如何检查发动机机油液位？");

    await user.click(screen.getByRole("checkbox", { name: "第 657 页" }));
    await user.click(screen.getByRole("button", { name: "开始查询" }));
    expect(await screen.findByText("机油检查 · PDF 第 657 页")).toBeInTheDocument();
  });
});
