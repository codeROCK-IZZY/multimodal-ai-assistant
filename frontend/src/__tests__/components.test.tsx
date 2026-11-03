import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Citations, ProviderTag, RichText, ToolEvents, ToolRequestCard } from "../components/chat";
import type { Citation, ToolRequest } from "../api/types";

const cites: Citation[] = [
  { n: 1, source: "chunk", attachment_id: "a1", filename: "travel-policy.pdf", locator: "page 2", quote: "Business class is allowed for flights of 6 hours or longer.", score: 7.1 },
  { n: 2, source: "memory", attachment_id: null, filename: null, locator: "memory", quote: "My manager is Dana.", score: 3 },
];

describe("chat components", () => {
  it("renders the reply markdown subset without injecting HTML", () => {
    const { container } = render(<RichText text={"Key points [1]:\n- **bold** item\n- <img src=x onerror=alert(1)>"} />);
    expect(container.querySelector("strong")).toHaveTextContent("bold");
    expect(container.querySelectorAll("li")).toHaveLength(2);
    expect(container.querySelector("img")).toBeNull();
    expect(screen.getByRole("button", { name: "Show source 1" })).toBeInTheDocument();
  });

  it("shows where each citation came from and its quote on demand", async () => {
    const onSelect = vi.fn();
    const { rerender } = render(<Citations items={cites} active={null} onSelect={onSelect} />);
    expect(screen.getByText(/travel-policy.pdf · page 2/)).toBeInTheDocument();
    expect(screen.getByText(/your memory/)).toBeInTheDocument();
    await userEvent.click(screen.getByText(/travel-policy.pdf/));
    expect(onSelect).toHaveBeenCalledWith(1);
    rerender(<Citations items={cites} active={1} onSelect={onSelect} />);
    expect(screen.getByText(/flights of 6 hours/)).toBeInTheDocument();
  });

  it("labels tool events in plain language", () => {
    render(<ToolEvents events={[
      { name: "calculator", args: {}, status: "executed" },
      { name: "create_task", args: {}, status: "needs_permission" },
      { name: "forget_memory", args: {}, status: "denied" },
    ]} />);
    expect(screen.getByText("Calculator · ran")).toBeInTheDocument();
    expect(screen.getByText("Create task · awaiting confirmation")).toBeInTheDocument();
    expect(screen.getByText("Forget memory · blocked by settings")).toBeInTheDocument();
  });

  it("asks for confirmation before a data-changing tool runs", async () => {
    const onDecide = vi.fn();
    const req: ToolRequest = { id: "r1", conversation_id: "c", message_id: "m", tool: "create_task",
      args: { title: "Call the plumber", due: "2026-11-03" }, status: "pending", result: null, created_at: "" };
    render(<ToolRequestCard request={req} busy={false} onDecide={onDecide} />);
    expect(screen.getByText("Call the plumber · due 2026-11-03")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Decline" }));
    await userEvent.click(screen.getByRole("button", { name: "Confirm" }));
    expect(onDecide.mock.calls).toEqual([[false], [true]]);
  });

  it("distinguishes Claude answers from local ones", () => {
    const { rerender } = render(<ProviderTag provider="claude:claude-opus-5-5" />);
    expect(screen.getByText("Claude · claude-opus-5-5")).toBeInTheDocument();
    rerender(<ProviderTag provider="local" />);
    expect(screen.getByText("Local")).toBeInTheDocument();
  });
});
