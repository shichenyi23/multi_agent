from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
from typing import Any, Protocol
import urllib.error
import urllib.request

from contracts import ModuleSpec


@dataclass(slots=True)
class PromptRequest:
    kind: str
    system_prompt: str
    user_prompt: str
    metadata: dict[str, Any] = field(default_factory=dict)


class LLMBackend(Protocol):
    name: str

    def generate(self, request: PromptRequest) -> str | None:
        """Return model output as raw text."""


def create_backend(name: str | None = None) -> LLMBackend:
    backend_name = (name or os.getenv("LLM4EDA_BACKEND") or "rule-based").strip().lower()
    if backend_name in {"openai-compatible", "openai", "chat-completions"}:
        api_key = os.getenv("LLM4EDA_API_KEY") or os.getenv("OPENAI_API_KEY")
        base_url = os.getenv("LLM4EDA_API_BASE") or "https://api.openai.com/v1"
        model = os.getenv("LLM4EDA_MODEL") or ""
        if api_key and model:
            return OpenAICompatibleBackend(api_key=api_key, base_url=base_url, model=model)
    return RuleBasedBackend()


@dataclass(slots=True)
class OpenAICompatibleBackend:
    api_key: str
    base_url: str
    model: str
    temperature: float = 0.1
    name: str = "openai-compatible"

    def generate(self, request: PromptRequest) -> str | None:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": request.system_prompt},
                {"role": "user", "content": request.user_prompt},
            ],
            "temperature": self.temperature,
        }
        endpoint = self.base_url.rstrip("/") + "/chat/completions"
        http_request = urllib.request.Request(
            endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(http_request, timeout=120) as response:
                body = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            return None

        choices = body.get("choices", [])
        if not choices:
            return None
        message = choices[0].get("message", {})
        content = message.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = [item.get("text", "") for item in content if isinstance(item, dict)]
            return "\n".join(part for part in parts if part)
        return None


@dataclass(slots=True)
class RuleBasedBackend:
    name: str = "rule-based"

    def generate(self, request: PromptRequest) -> str | None:
        kind = request.kind
        if kind == "spec_from_request":
            return self._generate_spec_from_request(request.metadata)
        if kind in {"rtl_generate", "rtl_repair"}:
            return self._generate_rtl(request.metadata)
        if kind in {"tb_generate", "tb_repair"}:
            return self._generate_tb(request.metadata)
        return None

    def _generate_spec_from_request(self, metadata: dict[str, Any]) -> str | None:
        request_text = str(metadata.get("request_text", ""))
        normalized = request_text.lower()
        if "ram" not in normalized:
            return None
        module_name = metadata.get("module_name_hint") or "ram_sp"
        spec = {
            "module_name": module_name,
            "summary": "Single-port synchronous RAM inferred from request text.",
            "ports": [
                {"name": "clk", "dir": "input", "width": 1},
                {"name": "rst_n", "dir": "input", "width": 1},
                {"name": "we", "dir": "input", "width": 1},
                {"name": "addr", "dir": "input", "width": "ADDR_WIDTH"},
                {"name": "din", "dir": "input", "width": "DATA_WIDTH"},
                {"name": "dout", "dir": "output", "width": "DATA_WIDTH"},
            ],
            "parameters": [
                {"name": "ADDR_WIDTH", "default": 8},
                {"name": "DATA_WIDTH", "default": 32},
                {"name": "DEPTH", "default": "1 << ADDR_WIDTH"},
            ],
            "clock_strategy": "posedge_clk",
            "reset_strategy": "sync_active_low",
            "timing_requirements": ["read_latency=1", "write_latency=1"],
            "constraints": ["write_priority=true"],
            "functional_spec": "Single-port synchronous RAM with write-first behavior.",
            "test_points": [
                "reset initializes observable state",
                "write then read same address returns the new data",
                "lowest and highest addresses are both accessible",
                "back-to-back writes to different addresses are preserved",
            ],
            "submodules": [],
        }
        return json.dumps(spec, ensure_ascii=False, indent=2)

    def _generate_rtl(self, metadata: dict[str, Any]) -> str | None:
        spec = self._spec_from_metadata(metadata)
        if spec is None or not self._is_single_port_ram(spec):
            return None

        addr_width = self._param_default(spec, "ADDR_WIDTH", 8)
        data_width = self._param_default(spec, "DATA_WIDTH", 32)
        depth = self._param_default(spec, "DEPTH", "1 << ADDR_WIDTH")
        reset_name = self._find_reset_name(spec) or "rst_n"
        reset_cond = f"!{reset_name}" if "low" in spec.reset_strategy else reset_name
        return "\n".join(
            [
                "`timescale 1ns/1ps",
                "/* verilator lint_off DECLFILENAME */",
                f"module {spec.module_name} #(",
                f"  parameter ADDR_WIDTH = {addr_width},",
                f"  parameter DATA_WIDTH = {data_width},",
                f"  parameter DEPTH = {depth}",
                ") (",
                "  input  wire                  clk,",
                f"  input  wire                  {reset_name},",
                "  input  wire                  we,",
                "  input  wire [ADDR_WIDTH-1:0] addr,",
                "  input  wire [DATA_WIDTH-1:0] din,",
                "  output reg  [DATA_WIDTH-1:0] dout",
                ");",
                "",
                "  reg [DATA_WIDTH-1:0] mem [0:DEPTH-1];",
                "  integer idx;",
                "",
                "  always @(posedge clk) begin",
                f"    if ({reset_cond}) begin",
                "      dout <= {DATA_WIDTH{1'b0}};",
                "      for (idx = 0; idx < DEPTH; idx = idx + 1) begin",
                "        mem[idx] <= {DATA_WIDTH{1'b0}};",
                "      end",
                "    end else if (we) begin",
                "      mem[addr] <= din;",
                "      dout <= din;",
                "    end else begin",
                "      dout <= mem[addr];",
                "    end",
                "  end",
                "",
                "endmodule",
                "/* verilator lint_on DECLFILENAME */",
                "",
            ]
        )

    def _generate_tb(self, metadata: dict[str, Any]) -> str | None:
        spec = self._spec_from_metadata(metadata)
        if spec is None or not self._is_single_port_ram(spec):
            return None

        reset_name = self._find_reset_name(spec) or "rst_n"
        reset_active = "1'b0" if "low" in spec.reset_strategy else "1'b1"
        reset_inactive = "1'b1" if reset_active == "1'b0" else "1'b0"
        return "\n".join(
            [
                "`timescale 1ns/1ps",
                "/* verilator lint_off DECLFILENAME */",
                f"module {spec.module_name}_tb;",
                "",
                "  localparam ADDR_WIDTH = 8;",
                "  localparam DATA_WIDTH = 32;",
                "  localparam DEPTH = 1 << ADDR_WIDTH;",
                "",
                "  reg clk;",
                f"  reg {reset_name};",
                "  reg we;",
                "  reg [ADDR_WIDTH-1:0] addr;",
                "  reg [DATA_WIDTH-1:0] din;",
                "  wire [DATA_WIDTH-1:0] dout;",
                "",
                "  integer error_count;",
                "",
                f"  {spec.module_name} #(",
                "    .ADDR_WIDTH(ADDR_WIDTH),",
                "    .DATA_WIDTH(DATA_WIDTH),",
                "    .DEPTH(DEPTH)",
                "  ) dut (",
                "    .clk(clk),",
                f"    .{reset_name}({reset_name}),",
                "    .we(we),",
                "    .addr(addr),",
                "    .din(din),",
                "    .dout(dout)",
                "  );",
                "",
                "  initial begin",
                "    clk = 1'b0;",
                "  end",
                "",
                "  always #5 clk = ~clk;",
                "",
                "  task automatic expect_eq(",
                "    input [DATA_WIDTH-1:0] expected,",
                "    input [DATA_WIDTH-1:0] actual,",
                "    input [255:0] label",
                "  );",
                "    begin",
                "      if (actual !== expected) begin",
                '        $display("FAIL: %0s expected=%0h actual=%0h time=%0t", label, expected, actual, $time);',
                "        error_count = error_count + 1;",
                "      end",
                "    end",
                "  endtask",
                "",
                "  task automatic drive_write(",
                "    input [ADDR_WIDTH-1:0] waddr,",
                "    input [DATA_WIDTH-1:0] wdata",
                "  );",
                "    begin",
                "      @(negedge clk);",
                "      we = 1'b1;",
                "      addr = waddr;",
                "      din = wdata;",
                "      @(posedge clk);",
                "      #1;",
                '      expect_eq(wdata, dout, "write-first behavior");',
                "    end",
                "  endtask",
                "",
                "  task automatic drive_read(",
                "    input [ADDR_WIDTH-1:0] raddr,",
                "    input [DATA_WIDTH-1:0] expected,",
                "    input [255:0] label",
                "  );",
                "    begin",
                "      @(negedge clk);",
                "      we = 1'b0;",
                "      addr = raddr;",
                "      din = {DATA_WIDTH{1'b0}};",
                "      @(posedge clk);",
                "      #1;",
                "      expect_eq(expected, dout, label);",
                "    end",
                "  endtask",
                "",
                "  initial begin",
                '    $dumpfile("wave.vcd");',
                f"    $dumpvars(0, {spec.module_name}_tb);",
                "    error_count = 0;",
                f"    {reset_name} = {reset_active};",
                "    we = 1'b0;",
                "    addr = {ADDR_WIDTH{1'b0}};",
                "    din = {DATA_WIDTH{1'b0}};",
                "",
                "    repeat (2) @(posedge clk);",
                "    @(negedge clk);",
                f"    {reset_name} = {reset_inactive};",
                "",
                '    drive_read({ADDR_WIDTH{1\'b0}}, {DATA_WIDTH{1\'b0}}, "reset clears address 0");',
                '    drive_write({ADDR_WIDTH{1\'b0}}, {{(DATA_WIDTH-1){1\'b0}}, 1\'b1});',
                '    drive_read({ADDR_WIDTH{1\'b0}}, {{(DATA_WIDTH-1){1\'b0}}, 1\'b1}, "readback low address");',
                '    drive_write({ADDR_WIDTH{1\'b1}}, {DATA_WIDTH{1\'b1}});',
                '    drive_read({ADDR_WIDTH{1\'b1}}, {DATA_WIDTH{1\'b1}}, "readback high address");',
                '    drive_write({{(ADDR_WIDTH-1){1\'b0}}, 1\'b1}, {{(DATA_WIDTH-2){1\'b0}}, 2\'b10});',
                '    drive_write({{(ADDR_WIDTH-2){1\'b0}}, 2\'b10}, {{(DATA_WIDTH-2){1\'b0}}, 2\'b11});',
                '    drive_read({{(ADDR_WIDTH-1){1\'b0}}, 1\'b1}, {{(DATA_WIDTH-2){1\'b0}}, 2\'b10}, "addr1 keeps data");',
                '    drive_read({{(ADDR_WIDTH-2){1\'b0}}, 2\'b10}, {{(DATA_WIDTH-2){1\'b0}}, 2\'b11}, "addr2 keeps data");',
                "",
                "    if (error_count == 0) begin",
                '      $display("PASS");',
                "    end else begin",
                '      $display("FAIL: error_count=%0d", error_count);',
                "    end",
                "    $finish;",
                "  end",
                "",
                "endmodule",
                "/* verilator lint_on DECLFILENAME */",
                "",
            ]
        )

    @staticmethod
    def _spec_from_metadata(metadata: dict[str, Any]) -> ModuleSpec | None:
        spec_obj = metadata.get("spec")
        if isinstance(spec_obj, ModuleSpec):
            return spec_obj
        return None

    @staticmethod
    def _param_default(spec: ModuleSpec, name: str, fallback: int | str) -> int | str:
        for parameter in spec.parameters:
            if parameter.name == name:
                return parameter.default
        return fallback

    @staticmethod
    def _find_reset_name(spec: ModuleSpec) -> str | None:
        for port in spec.ports:
            if "rst" in port.name:
                return port.name
        return None

    @staticmethod
    def _is_single_port_ram(spec: ModuleSpec) -> bool:
        normalized = f"{spec.module_name} {spec.functional_spec}".lower()
        port_names = {port.name for port in spec.ports}
        return "ram" in normalized and {"clk", "we", "addr", "din", "dout"}.issubset(port_names)
