`timescale 1ns/1ps
/* verilator lint_off DECLFILENAME */
module ram_sp #(
  parameter ADDR_WIDTH = 8,
  parameter DATA_WIDTH = 32,
  parameter DEPTH = 1 << ADDR_WIDTH
) (
  input  wire                  clk,
  input  wire                  rst_n,
  input  wire                  we,
  input  wire [ADDR_WIDTH-1:0] addr,
  input  wire [DATA_WIDTH-1:0] din,
  output reg  [DATA_WIDTH-1:0] dout
);

  reg [DATA_WIDTH-1:0] mem [0:DEPTH-1];
  integer idx;

  always @(posedge clk) begin
    if (!rst_n) begin
      dout <= {DATA_WIDTH{1'b0}};
      /* verilator lint_off BLKSEQ */
      for (idx = 0; idx < DEPTH; idx = idx + 1) begin
        mem[idx] = {DATA_WIDTH{1'b0}};
      end
      /* verilator lint_on BLKSEQ */
    end else if (we) begin
      mem[addr] <= din;
      dout <= din;
    end else begin
      dout <= mem[addr];
    end
  end

endmodule
/* verilator lint_on DECLFILENAME */
