`timescale 1ns/1ps
/* verilator lint_off DECLFILENAME */
module ram_sp_tb;

  localparam ADDR_WIDTH = 8;
  localparam DATA_WIDTH = 32;
  localparam DEPTH = 1 << ADDR_WIDTH;

  reg clk;
  reg rst_n;
  reg we;
  reg [ADDR_WIDTH-1:0] addr;
  reg [DATA_WIDTH-1:0] din;
  wire [DATA_WIDTH-1:0] dout;

  integer error_count;

  ram_sp #(
    .ADDR_WIDTH(ADDR_WIDTH),
    .DATA_WIDTH(DATA_WIDTH),
    .DEPTH(DEPTH)
  ) dut (
    .clk(clk),
    .rst_n(rst_n),
    .we(we),
    .addr(addr),
    .din(din),
    .dout(dout)
  );

  initial begin
    clk = 1'b0;
  end

  /* verilator lint_off BLKSEQ */
  always #5 clk = ~clk;
  /* verilator lint_on BLKSEQ */

  task automatic expect_eq(
    input [DATA_WIDTH-1:0] expected,
    input [DATA_WIDTH-1:0] actual,
    input [255:0] label
  );
    begin
      if (actual !== expected) begin
        $display("FAIL: %0s expected=%0h actual=%0h time=%0t", label, expected, actual, $time);
        error_count = error_count + 1;
      end
    end
  endtask

  task automatic drive_write(
    input [ADDR_WIDTH-1:0] waddr,
    input [DATA_WIDTH-1:0] wdata
  );
    begin
      @(negedge clk);
      we = 1'b1;
      addr = waddr;
      din = wdata;
      @(posedge clk);
      #1;
      expect_eq(wdata, dout, "write-first behavior");
    end
  endtask

  task automatic drive_read(
    input [ADDR_WIDTH-1:0] raddr,
    input [DATA_WIDTH-1:0] expected,
    input [255:0] label
  );
    begin
      @(negedge clk);
      we = 1'b0;
      addr = raddr;
      din = {DATA_WIDTH{1'b0}};
      @(posedge clk);
      #1;
      expect_eq(expected, dout, label);
    end
  endtask

  initial begin
    $dumpfile("wave.vcd");
    $dumpvars(0, ram_sp_tb);
    error_count = 0;
    rst_n = 1'b0;
    we = 1'b0;
    addr = {ADDR_WIDTH{1'b0}};
    din = {DATA_WIDTH{1'b0}};

    repeat (2) @(posedge clk);
    @(negedge clk);
    rst_n = 1'b1;

    drive_read({ADDR_WIDTH{1'b0}}, {DATA_WIDTH{1'b0}}, "reset clears address 0");
    drive_write({ADDR_WIDTH{1'b0}}, {{(DATA_WIDTH-1){1'b0}}, 1'b1});
    drive_read({ADDR_WIDTH{1'b0}}, {{(DATA_WIDTH-1){1'b0}}, 1'b1}, "readback low address");
    drive_write({ADDR_WIDTH{1'b1}}, {DATA_WIDTH{1'b1}});
    drive_read({ADDR_WIDTH{1'b1}}, {DATA_WIDTH{1'b1}}, "readback high address");
    drive_write({{(ADDR_WIDTH-1){1'b0}}, 1'b1}, {{(DATA_WIDTH-2){1'b0}}, 2'b10});
    drive_write({{(ADDR_WIDTH-2){1'b0}}, 2'b10}, {{(DATA_WIDTH-2){1'b0}}, 2'b11});
    drive_read({{(ADDR_WIDTH-1){1'b0}}, 1'b1}, {{(DATA_WIDTH-2){1'b0}}, 2'b10}, "addr1 keeps data");
    drive_read({{(ADDR_WIDTH-2){1'b0}}, 2'b10}, {{(DATA_WIDTH-2){1'b0}}, 2'b11}, "addr2 keeps data");

    if (error_count == 0) begin
      $display("PASS");
    end else begin
      $display("FAIL: error_count=%0d", error_count);
    end
    $finish;
  end

endmodule
/* verilator lint_on DECLFILENAME */
