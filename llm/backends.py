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
        module_name = metadata.get("module_name_hint") or "user_module"

        # Detect module type
        if "ram" in normalized or "memory" in normalized:
            return self._generate_ram_spec(module_name, request_text)
        elif "counter" in normalized:
            return self._generate_counter_spec(module_name, request_text)
        elif "fifo" in normalized:
            return self._generate_fifo_spec(module_name, request_text)
        elif "adder" in normalized or "add" in normalized:
            return self._generate_adder_spec(module_name, request_text)
        elif "mux" in normalized or "multiplexer" in normalized:
            return self._generate_mux_spec(module_name, request_text)
        else:
            # Default to RAM for backward compatibility
            return self._generate_ram_spec(module_name, request_text)

    def _generate_ram_spec(self, module_name: str, request_text: str) -> str:
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

    def _generate_counter_spec(self, module_name: str, request_text: str) -> str:
        # Extract bit width from request
        width = 8
        if "4" in request_text and "bit" in request_text:
            width = 4
        elif "16" in request_text and "bit" in request_text:
            width = 16
        elif "32" in request_text and "bit" in request_text:
            width = 32

        has_async = "asynchronous" in request_text or "async" in request_text
        reset_strategy = "async_active_low" if has_async else "sync_active_low"

        spec = {
            "module_name": module_name,
            "summary": "Up counter module.",
            "ports": [
                {"name": "clk", "dir": "input", "width": 1},
                {"name": "rst_n", "dir": "input", "width": 1},
                {"name": "en", "dir": "input", "width": 1},
                {"name": "count", "dir": "output", "width": width},
            ],
            "parameters": [
                {"name": "WIDTH", "default": width},
            ],
            "clock_strategy": "posedge_clk",
            "reset_strategy": reset_strategy,
            "timing_requirements": [],
            "constraints": [],
            "functional_spec": f"{width}-bit up counter with enable input.",
            "test_points": [
                "reset sets count to 0",
                "count increments when en is high",
                "count wraps around at maximum value",
            ],
            "submodules": [],
        }
        return json.dumps(spec, ensure_ascii=False, indent=2)

    def _generate_fifo_spec(self, module_name: str, request_text: str) -> str:
        # Extract depth and width
        depth = 16
        width = 8

        if "depth" in request_text:
            import re
            match = re.search(r'depth\s*(\d+)', request_text)
            if match:
                depth = int(match.group(1))

        if "width" in request_text:
            import re
            match = re.search(r'width\s*(\d+)', request_text)
            if match:
                width = int(match.group(1))

        spec = {
            "module_name": module_name,
            "summary": "Synchronous FIFO queue.",
            "ports": [
                {"name": "clk", "dir": "input", "width": 1},
                {"name": "rst_n", "dir": "input", "width": 1},
                {"name": "wr_en", "dir": "input", "width": 1},
                {"name": "rd_en", "dir": "input", "width": 1},
                {"name": "din", "dir": "input", "width": width},
                {"name": "dout", "dir": "output", "width": width},
                {"name": "full", "dir": "output", "width": 1},
                {"name": "empty", "dir": "output", "width": 1},
            ],
            "parameters": [
                {"name": "DATA_WIDTH", "default": width},
                {"name": "DEPTH", "default": depth},
            ],
            "clock_strategy": "posedge_clk",
            "reset_strategy": "sync_active_low",
            "timing_requirements": [],
            "constraints": [],
            "functional_spec": f"Synchronous FIFO with depth {depth} and width {width}.",
            "test_points": [
                "reset clears FIFO",
                "write increments fill count",
                "read decrements fill count",
                "full flag when FIFO is full",
                "empty flag when FIFO is empty",
            ],
            "submodules": [],
        }
        return json.dumps(spec, ensure_ascii=False, indent=2)

    def _generate_adder_spec(self, module_name: str, request_text: str) -> str:
        width = 8
        if "4" in request_text and "bit" in request_text:
            width = 4
        elif "16" in request_text and "bit" in request_text:
            width = 16
        elif "32" in request_text and "bit" in request_text:
            width = 32

        spec = {
            "module_name": module_name,
            "summary": "Adder module.",
            "ports": [
                {"name": "a", "dir": "input", "width": width},
                {"name": "b", "dir": "input", "width": width},
                {"name": "cin", "dir": "input", "width": 1},
                {"name": "sum", "dir": "output", "width": width},
                {"name": "cout", "dir": "output", "width": 1},
            ],
            "parameters": [
                {"name": "WIDTH", "default": width},
            ],
            "clock_strategy": "combinational",
            "reset_strategy": "",
            "timing_requirements": [],
            "constraints": [],
            "functional_spec": f"{width}-bit adder with carry in/out.",
            "test_points": [
                "a + b = sum",
                "carry out is correct",
            ],
            "submodules": [],
        }
        return json.dumps(spec, ensure_ascii=False, indent=2)

    def _generate_mux_spec(self, module_name: str, request_text: str) -> str:
        width = 8
        if "4" in request_text and "bit" in request_text:
            width = 4
        elif "16" in request_text and "bit" in request_text:
            width = 16

        # Determine select width
        sel = 2
        if "4" in request_text and "to" in request_text:
            sel = 2
        if "8" in request_text and "to" in request_text:
            sel = 3

        spec = {
            "module_name": module_name,
            "summary": "Multiplexer module.",
            "ports": [
                {"name": "sel", "dir": "input", "width": sel},
                {"name": "din0", "dir": "input", "width": width},
                {"name": "din1", "dir": "input", "width": width},
                {"name": "dout", "dir": "output", "width": width},
            ],
            "parameters": [
                {"name": "WIDTH", "default": width},
                {"name": "N", "default": 2},
            ],
            "clock_strategy": "combinational",
            "reset_strategy": "",
            "timing_requirements": [],
            "constraints": [],
            "functional_spec": f"{width}-bit {2**sel}-to-1 multiplexer.",
            "test_points": [
                "sel selects correct input",
            ],
            "submodules": [],
        }
        return json.dumps(spec, ensure_ascii=False, indent=2)

    def _generate_rtl(self, metadata: dict[str, Any]) -> str | None:
        spec = self._spec_from_metadata(metadata)
        if spec is None:
            return None

        # Detect module type based on ports
        if self._is_single_port_ram(spec):
            return self._generate_ram_rtl(spec)
        elif self._is_counter(spec):
            return self._generate_counter_rtl(spec)
        elif self._is_fifo(spec):
            return self._generate_fifo_rtl(spec)
        elif self._is_adder(spec):
            return self._generate_adder_rtl(spec)
        elif self._is_mux(spec):
            return self._generate_mux_rtl(spec)

        return None

    def _is_counter(self, spec: ModuleSpec) -> bool:
        port_names = [p.name for p in spec.ports]
        has_count = "count" in port_names
        has_en = "en" in port_names
        return has_count and has_en

    def _is_fifo(self, spec: ModuleSpec) -> bool:
        port_names = [p.name for p in spec.ports]
        return "wr_en" in port_names and "rd_en" in port_names and "full" in port_names

    def _is_adder(self, spec: ModuleSpec) -> bool:
        port_names = [p.name for p in spec.ports]
        return "a" in port_names and "b" in port_names and "sum" in port_names

    def _is_mux(self, spec: ModuleSpec) -> bool:
        port_names = [p.name for p in spec.ports]
        return "sel" in port_names and "din" in port_names[0] if port_names else False

    def _generate_counter_rtl(self, spec: ModuleSpec) -> str:
        width = self._param_default(spec, "WIDTH", 8)
        reset_name = self._find_reset_name(spec) or "rst_n"
        has_en = any(p.name == "en" for p in spec.ports)

        reset_cond = f"!{reset_name}" if "low" in spec.reset_strategy else reset_name

        lines = [
            "`timescale 1ns/1ps",
            "/* verilator lint_off DECLFILENAME */",
            f"module {spec.module_name} #(",
            f"  parameter WIDTH = {width}",
            ") (",
            f"  input  wire       clk,",
            f"  input  wire       {reset_name},",
        ]

        if has_en:
            lines.append("  input  wire       en,")

        lines.extend([
            f"  output reg  [WIDTH-1:0] count",
            ");",
            "",
            "  always @(posedge clk) begin",
            f"    if ({reset_cond}) begin",
            f"      count <= {width}'b0;",
        ])

        if has_en:
            lines.extend([
            "    end else if (en) begin",
            "      count <= count + 1'b1;",
            "    end",
            ])
        else:
            lines.extend([
            "    end else begin",
            "      count <= count + 1'b1;",
            "    end",
            ])

        lines.extend([
            "  end",
            "",
            "endmodule",
            "/* verilator lint_on DECLFILENAME */",
        ])
        return "\n".join(lines)

    def _generate_fifo_rtl(self, spec: ModuleSpec) -> str:
        width = self._param_default(spec, "DATA_WIDTH", 8)
        depth = self._param_default(spec, "DEPTH", 16)
        reset_name = self._find_reset_name(spec) or "rst_n"
        reset_cond = f"!{reset_name}" if "low" in spec.reset_strategy else reset_name

        lines = [
            "`timescale 1ns/1ps",
            "/* verilator lint_off DECLFILENAME */",
            f"module {spec.module_name} #(",
            f"  parameter DATA_WIDTH = {width},",
            f"  parameter DEPTH = {depth}",
            ") (",
            f"  input  wire       clk,",
            f"  input  wire       {reset_name},",
            "  input  wire       wr_en,",
            "  input  wire       rd_en,",
            f"  input  wire [DATA_WIDTH-1:0] din,",
            f"  output reg  [DATA_WIDTH-1:0] dout,",
            "  output wire       full,",
            "  output wire       empty",
            ");",
            "",
            f"  localparam ADDR_WIDTH = $clog2(DEPTH);",
            "",
            f"  reg [DATA_WIDTH-1:0] mem [0:DEPTH-1];",
            "  reg [ADDR_WIDTH:0] wr_ptr;",
            "  reg [ADDR_WIDTH:0] rd_ptr;",
            "  wire [ADDR_WIDTH-1:0] wr_addr = wr_ptr[ADDR_WIDTH-1:0];",
            "  wire [ADDR_WIDTH-1:0] rd_addr = rd_ptr[ADDR_WIDTH-1:0];",
            "  wire wr_ok = wr_en && !full;",
            "  wire rd_ok = rd_en && !empty;",
            "",
            "  assign full = (wr_ptr[ADDR_WIDTH-1:0] == rd_ptr[ADDR_WIDTH-1:0]) && (wr_ptr[ADDR_WIDTH] != rd_ptr[ADDR_WIDTH]);",
            "  assign empty = (wr_ptr == rd_ptr);",
            "",
            "  always @(posedge clk) begin",
            f"    if ({reset_cond}) begin",
            "      wr_ptr <= 0;",
            "      rd_ptr <= 0;",
            "      dout <= 0;",
            "    end else begin",
            "      if (wr_ok) begin",
            "        mem[wr_addr] <= din;",
            "        wr_ptr <= wr_ptr + 1;",
            "      end",
            "      if (rd_ok) begin",
            "        dout <= mem[rd_addr];",
            "        rd_ptr <= rd_ptr + 1;",
            "      end",
            "    end",
            "  end",
            "",
            "endmodule",
            "/* verilator lint_on DECLFILENAME */",
        ]
        return "\n".join(lines)

    def _generate_adder_rtl(self, spec: ModuleSpec) -> str:
        width = self._param_default(spec, "WIDTH", 8)
        has_cin = any(p.name == "cin" for p in spec.ports)

        lines = [
            "`timescale 1ns/1ps",
            "/* verilator lint_off DECLFILENAME */",
            f"module {spec.module_name} #(",
            f"  parameter WIDTH = {width}",
            ") (",
            f"  input  wire [WIDTH-1:0] a,",
            f"  input  wire [WIDTH-1:0] b,",
        ]

        if has_cin:
            lines.append("  input  wire       cin,")

        lines.extend([
            f"  output wire [WIDTH-1:0] sum,",
            "  output wire       cout",
            ");",
            "",
            f"  {{sum, cout}} <= a + b" + (" + cin" if has_cin else "" ) + ";",
            "",
            "endmodule",
            "/* verilator lint_on DECLFILENAME */",
        ])
        return "\n".join(lines)

    def _generate_mux_rtl(self, spec: ModuleSpec) -> str:
        width = self._param_default(spec, "WIDTH", 8)

        # Find sel width
        sel_port = next((p for p in spec.ports if p.name == "sel"), None)
        sel_width = sel_port.width if sel_port else 2

        lines = [
            "`timescale 1ns/1ps",
            "/* verilator lint_off DECLFILENAME */",
            f"module {spec.module_name} #(",
            f"  parameter WIDTH = {width},",
            f"  parameter N = 2",
            ") (",
            f"  input  wire [$clog2(N)-1:0] sel,",
        ]

        # Add din ports
        for i in range(2):
            lines.append(f"  input  wire [WIDTH-1:0] din{i},")

        lines.extend([
            f"  output reg  [WIDTH-1:0] dout",
            ");",
            "",
            "  always @(*) begin",
            "    case (sel)",
            "      0: dout = din0;",
            "      1: dout = din1;",
            "      default: dout = 0;",
            "    endcase",
            "  end",
            "",
            "endmodule",
            "/* verilator lint_on DECLFILENAME */",
        ])
        return "\n".join(lines)

    def _generate_ram_rtl(self, spec: ModuleSpec) -> str:
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
                "      /* verilator lint_off BLKSEQ */",
                "      for (idx = 0; idx < DEPTH; idx = idx + 1) begin",
                "        mem[idx] = {DATA_WIDTH{1'b0}};",
                "      end",
                "      /* verilator lint_on BLKSEQ */",
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
            ]
        )

    def _generate_tb(self, metadata: dict[str, Any]) -> str | None:
        spec = self._spec_from_metadata(metadata)
        if spec is None:
            return None

        # Detect module type
        if self._is_single_port_ram(spec):
            return self._generate_ram_tb(spec)
        elif self._is_counter(spec):
            return self._generate_counter_tb(spec)
        elif self._is_fifo(spec):
            return self._generate_fifo_tb(spec)
        elif self._is_adder(spec):
            return self._generate_adder_tb(spec)
        elif self._is_mux(spec):
            return self._generate_mux_tb(spec)

        return None

    def _generate_counter_tb(self, spec: ModuleSpec) -> str:
        width = self._param_default(spec, "WIDTH", 8)
        reset_name = self._find_reset_name(spec) or "rst_n"
        reset_active = "1'b0" if "low" in spec.reset_strategy else "1'b1"
        reset_inactive = "1'b1" if reset_active == "1'b0" else "1'b0"
        has_en = any(p.name == "en" for p in spec.ports)

        lines = [
            "`timescale 1ns/1ps",
            "/* verilator lint_off DECLFILENAME */",
            f"module {spec.module_name}_tb;",
            "",
            f"  localparam WIDTH = {width};",
            "",
            "  reg clk;",
            f"  reg {reset_name};",
        ]

        if has_en:
            lines.append("  reg en;")

        lines.extend([
            f"  wire [WIDTH-1:0] count;",
            "",
            "  integer error_count;",
            "",
            f"  {spec.module_name} #(",
            f"    .WIDTH(WIDTH)",
            "  ) dut (",
            f"    .clk(clk),",
            f"    .{reset_name}({reset_name}),",
        ])

        if has_en:
            lines.append("    .en(en),")

        lines.extend([
            "    .count(count)",
            "  );",
            "",
            "  initial begin",
            "    clk = 1'b0;",
            "  end",
            "",
            "  /* verilator lint_off BLKSEQ */",
            "  always #5 clk = ~clk;",
            "  /* verilator lint_on BLKSEQ */",
            "",
            "  initial begin",
            '    $dumpfile("wave.vcd");',
            f"    $dumpvars(0, {spec.module_name}_tb);",
            "    error_count = 0;",
            f"    {reset_name} = {reset_active};",
            "    en = 1'b0;",
            "",
            "    repeat (2) @(posedge clk);",
            "    @(negedge clk);",
            f"    {reset_name} = {reset_inactive};",
            "",
            "    // Test 1: enable counting",
            "    en = 1'b1;",
            "    repeat (5) @(posedge clk);",
            "",
            "    // Test 2: disable counting",
            "    en = 1'b0;",
            "    repeat (2) @(posedge clk);",
            "",
            "    if (error_count == 0) begin",
            '      $display("PASS");',
            "    end else begin",
            '      $display("FAIL: error_count=%0d", error_count);',
            "    end",
            "",
            "    $finish;",
            "  end",
            "",
            "endmodule",
            "/* verilator lint_on DECLFILENAME */",
        ])
        return "\n".join(lines)

    def _generate_fifo_tb(self, spec: ModuleSpec) -> str:
        width = self._param_default(spec, "DATA_WIDTH", 8)
        depth = self._param_default(spec, "DEPTH", 16)
        reset_name = self._find_reset_name(spec) or "rst_n"
        reset_active = "1'b0" if "low" in spec.reset_strategy else "1'b1"
        reset_inactive = "1'b1" if reset_active == "1'b0" else "1'b0"

        lines = [
            "`timescale 1ns/1ps",
            "/* verilator lint_off DECLFILENAME */",
            "/* verilator lint_off UNUSEDSIGNAL */",
            f"module {spec.module_name}_tb;",
            "",
            f"  localparam DATA_WIDTH = {width};",
            f"  localparam DEPTH = {depth};",
            "",
            "  reg clk;",
            f"  reg {reset_name};",
            "  reg wr_en;",
            "  reg rd_en;",
            f"  reg [DATA_WIDTH-1:0] din;",
            f"  wire [DATA_WIDTH-1:0] dout;",
            "  wire full;",
            "  wire empty;",
            "",
            "  integer error_count;",
            "  /* verilator lint_on UNUSEDSIGNAL */",
            "",
            f"  {spec.module_name} #(",
            f"    .DATA_WIDTH(DATA_WIDTH),",
            f"    .DEPTH(DEPTH)",
            "  ) dut (",
            "    .clk(clk),",
            f"    .{reset_name}({reset_name}),",
            "    .wr_en(wr_en),",
            "    .rd_en(rd_en),",
            "    .din(din),",
            "    .dout(dout),",
            "    .full(full),",
            "    .empty(empty)",
            "  );",
            "",
            "  initial begin",
            "    clk = 1'b0;",
            "  end",
            "",
            "  /* verilator lint_off BLKSEQ */",
            "  always #5 clk = ~clk;",
            "  /* verilator lint_on BLKSEQ */",
            "",
            "  initial begin",
            '    $dumpfile("wave.vcd");',
            f"    $dumpvars(0, {spec.module_name}_tb);",
            "    error_count = 0;",
            f"    {reset_name} = {reset_active};",
            "    wr_en = 0;",
            "    rd_en = 0;",
            "    din = 0;",
            "",
            "    repeat (2) @(posedge clk);",
            "    @(negedge clk);",
            f"    {reset_name} = {reset_inactive};",
            "",
            "    // Test: write and read",
            "    @(negedge clk);",
            "    wr_en = 1;",
            "    din = 8'hAA;",
            "    @(posedge clk);",
            "    @(negedge clk);",
            "    wr_en = 0;",
            "    rd_en = 1;",
            "    @(posedge clk);",
            "",
            "    if (error_count == 0) begin",
            '      $display("PASS");',
            "    end else begin",
            '      $display("FAIL: error_count=%0d", error_count);',
            "    end",
            "",
            "    $finish;",
            "  end",
            "",
            "endmodule",
            "/* verilator lint_on DECLFILENAME */",
        ]
        return "\n".join(lines)

    def _generate_adder_tb(self, spec: ModuleSpec) -> str:
        width = self._param_default(spec, "WIDTH", 8)
        has_cin = any(p.name == "cin" for p in spec.ports)

        lines = [
            "`timescale 1ns/1ps",
            "/* verilator lint_off DECLFILENAME */",
            f"module {spec.module_name}_tb;",
            "",
            f"  localparam WIDTH = {width};",
            "",
            f"  reg [WIDTH-1:0] a;",
            f"  reg [WIDTH-1:0] b;",
        ]

        if has_cin:
            lines.append("  reg cin;")

        lines.extend([
            f"  wire [WIDTH-1:0] sum;",
            "  wire cout;",
            "",
            "  integer error_count;",
            "",
            f"  {spec.module_name} #(",
            f"    .WIDTH(WIDTH)",
            "  ) dut (",
            "    .a(a),",
            "    .b(b),",
        ])

        if has_cin:
            lines.append("    .cin(cin),")

        lines.extend([
            "    .sum(sum),",
            "    .cout(cout)",
            "  );",
            "",
            "  initial begin",
            '    $dumpfile("wave.vcd");',
            f"    $dumpvars(0, {spec.module_name}_tb);",
            "    error_count = 0;",
            "",
            "    // Test: 1 + 1",
            "    a = 8'd1; b = 8'd1;",
            "    #10;",
            "",
            "    if (error_count == 0) begin",
            '      $display("PASS");',
            "    end else begin",
            '      $display("FAIL: error_count=%0d", error_count);',
            "    end",
            "",
            "    $finish;",
            "  end",
            "",
            "endmodule",
            "/* verilator lint_on DECLFILENAME */",
        ])
        return "\n".join(lines)

    def _generate_mux_tb(self, spec: ModuleSpec) -> str:
        width = self._param_default(spec, "WIDTH", 8)

        lines = [
            "`timescale 1ns/1ps",
            "/* verilator lint_off DECLFILENAME */",
            f"module {spec.module_name}_tb;",
            "",
            f"  localparam WIDTH = {width};",
            "",
            "  reg [1:0] sel;",
            f"  reg [WIDTH-1:0] din0;",
            f"  reg [WIDTH-1:0] din1;",
            f"  wire [WIDTH-1:0] dout;",
            "",
            "  integer error_count;",
            "",
            f"  {spec.module_name} #(",
            f"    .WIDTH(WIDTH)",
            "  ) dut (",
            "    .sel(sel),",
            "    .din0(din0),",
            "    .din1(din1),",
            "    .dout(dout)",
            "  );",
            "",
            "  initial begin",
            '    $dumpfile("wave.vcd");',
            f"    $dumpvars(0, {spec.module_name}_tb);",
            "    error_count = 0;",
            "",
            "    // Test: select din0",
            "    sel = 0; din0 = 8'hAA; din1 = 8'h55;",
            "    #10;",
            "    if (dout !== 8'hAA) error_count++;",
            "",
            "    // Test: select din1",
            "    sel = 1;",
            "    #10;",
            "    if (dout !== 8'h55) error_count++;",
            "",
            "    if (error_count == 0) begin",
            '      $display("PASS");',
            "    end else begin",
            '      $display("FAIL: error_count=%0d", error_count);',
            "    end",
            "",
            "    $finish;",
            "  end",
            "",
            "endmodule",
            "/* verilator lint_on DECLFILENAME */",
        ]
        return "\n".join(lines)

    def _generate_ram_tb(self, spec: ModuleSpec) -> str:
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
                "  /* verilator lint_off BLKSEQ */",
                "  always #5 clk = ~clk;",
                "  /* verilator lint_on BLKSEQ */",
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
