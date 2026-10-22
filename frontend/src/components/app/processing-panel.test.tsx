import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ProcessingPanel, progressDetailFrom } from "./clip-wall";

describe("ProcessingPanel", () => {
  beforeEach(() => { vi.useFakeTimers(); vi.setSystemTime(new Date("2026-01-01T00:00:00Z")); });
  afterEach(() => { cleanup(); vi.useRealTimers(); });

  it("shows sub-progress, a running clock and the time left", () => {
    const detail = progressDetailFrom({ stage: "transcribe", stage_progress: 42.4, elapsed_seconds: 125, eta_seconds: 3700 });
    render(<ProcessingPanel status="processing" progress={38} message="Transcribing 42% — 54:10 / 129:00 of audio, ~1h 01m left" readyCount={0} detail={detail} />);

    expect(screen.getByText(/Transcribing 42%/)).toBeInTheDocument();
    expect(screen.getByRole("progressbar", { name: "42% of this step" })).toBeInTheDocument();
    expect(screen.getByText(/2:05 elapsed · about 1h 01m left/)).toBeInTheDocument();
    // The Transcribing step is the highlighted one even though 38% would also say so.
    expect(screen.getByText("Transcribing", { selector: "span" })).toHaveClass("font-medium");

    act(() => { vi.advanceTimersByTime(60_000); });
    expect(screen.getByText(/3:05 elapsed · about 1h 00m left/)).toBeInTheDocument();
  });

  it("only counts up while waiting for another transcription", () => {
    const detail = progressDetailFrom({ stage: "transcribe", stage_progress: 0, waiting: true, elapsed_seconds: 75 });
    render(<ProcessingPanel status="processing" progress={30} message="Waiting for another transcription… (1:15)" readyCount={0} detail={detail} />);

    expect(screen.getByText(/Waiting for another transcription/)).toBeInTheDocument();
    expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
    expect(screen.getByText("1:15 elapsed")).toBeInTheDocument();
  });

  it("renders as before without sub-progress (older backend)", () => {
    render(<ProcessingPanel status="processing" progress={30} message="Generating transcript..." readyCount={0} />);

    expect(screen.getByText("Generating transcript...")).toBeInTheDocument();
    expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
    expect(screen.queryByText(/elapsed/)).not.toBeInTheDocument();
    expect(screen.getByText("Transcribing", { selector: "span" })).toHaveClass("font-medium");
  });

  it("ignores events that carry no stage", () => {
    expect(progressDetailFrom({ progress: 50, message: "Analyzing content with AI..." })).toBeNull();
  });
});
