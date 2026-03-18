"""
LLM4EDA Multi-Agent GUI

A Streamlit-based GUI for the LLM4EDA multi-agent Verilog generation system.
Users can input natural language descriptions to generate Verilog modules and run tests.
"""

import streamlit as st
from datetime import datetime
from pathlib import Path

# Add project root to path
import sys
ROOT_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(ROOT_DIR))

# Use absolute paths for artifacts and logs
ARTIFACTS_DIR = ROOT_DIR / "artifacts"
LOGS_DIR = ROOT_DIR / "logs"

from workflow.state_machine import WorkflowOrchestrator
from workflow.artifact_store import ArtifactStore
from contracts import Stage

# Page configuration
st.set_page_config(
    page_title="LLM4EDA Multi-Agent",
    page_icon="🧪",
    layout="wide"
)

# Sidebar configuration
st.sidebar.title("⚙️ 配置")

# Log viewer in sidebar
st.sidebar.markdown("---")
st.sidebar.subheader("📋 日志")
if LOGS_DIR.exists():
    history_file = LOGS_DIR / "history.txt"
    if history_file.exists():
        history = history_file.read_text(encoding="utf-8")
        st.sidebar.text_area("运行历史", history, height=200, key="history_view")

    log_files = sorted(LOGS_DIR.glob("*.log"), key=lambda x: x.stat().st_mtime, reverse=True)
    if log_files:
        latest_log = log_files[0]
        log_content = latest_log.read_text(encoding="utf-8")
        st.sidebar.text_area(f"最新日志 ({latest_log.name})", log_content[-3000:], height=200)

st.sidebar.markdown("---")
backend_option = st.sidebar.selectbox(
    "LLM后端",
    ["rule-based", "openai-compatible"],
    index=0,
    help="rule-based用于本地测试，openai-compatible需要配置API"
)

max_lint_retries = st.sidebar.slider("最大Lint重试次数", 0, 5, 2)
max_sim_retries = st.sidebar.slider("最大仿真重试次数", 0, 5, 2)

if backend_option == "openai-compatible":
    st.sidebar.markdown("---")
    st.sidebar.markdown("### API配置")
    api_key = st.sidebar.text_input("API Key", type="password")
    api_base = st.sidebar.text_input("API Base URL", value="https://api.openai.com/v1")
    model = st.sidebar.text_input("Model", value="gpt-4")

    if api_key:
        import os
        os.environ["LLM4EDA_BACKEND"] = "openai-compatible"
        os.environ["LLM4EDA_API_KEY"] = api_key
        os.environ["LLM4EDA_API_BASE"] = api_base
        os.environ["LLM4EDA_MODEL"] = model

# Main content
st.title("🧪 LLM4EDA Multi-Agent")
st.markdown("""
使用自然语言描述你想要生成的硬件模块，系统会自动生成Verilog代码并进行测试。
""")

# Input section
col1, col2 = st.columns([2, 1])

with col1:
    user_input = st.text_area(
        "📝 描述你想要生成的硬件模块",
        height=150,
        placeholder="例如：生成一个8位计数器，带有异步复位功能..."
    )

with col2:
    st.markdown("### 💡 示例描述")
    examples = [
        "生成一个8位计数器，带有异步复位",
        "创建一个单端口同步RAM，数据宽度32位，地址宽度8位",
        "设计一个简单的FIFO队列，深度16，宽度8",
    ]
    for i, ex in enumerate(examples):
        if st.button(f"示例 {i+1}", key=f"example_{i}"):
            st.session_state.example_text = ex

    if "example_text" in st.session_state:
        user_input = st.session_state.example_text

# Generate button
if st.button("🚀 生成模块", type="primary", disabled=not user_input):
    # Create unique module directory
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    module_name = f"user_{timestamp}"
    module_dir = ARTIFACTS_DIR / module_name
    module_dir.mkdir(parents=True, exist_ok=True)

    # Setup logging
    LOGS_DIR.mkdir(exist_ok=True)
    log_file = LOGS_DIR / f"{timestamp}.log"

    # Create log handler
    import logging
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
    logger.addHandler(file_handler)

    # Save user request
    request_path = module_dir / "request.txt"
    request_path.write_text(user_input, encoding="utf-8")

    logging.info(f"开始生成模块: {module_name}")
    logging.info(f"用户输入: {user_input}")

    # Progress display
    progress_container = st.container()
    with progress_container:
        st.info(f"📂 工作目录: {module_dir}")
        st.info("🔄 正在初始化工作流...")

        # Create progress bar
        progress_bar = st.progress(0)
        status_text = st.empty()

    try:
        # Initialize orchestrator
        status_text.text("🔧 初始化Orchestrator...")
        progress_bar.progress(10)

        orchestrator = WorkflowOrchestrator(
            backend_name=backend_option if backend_option != "rule-based" else None,
            max_lint_retries=max_lint_retries,
            max_sim_retries=max_sim_retries,
        )

        # Run workflow
        status_text.text("📋 分析规格说明...")
        progress_bar.progress(20)
        logging.info("开始分析规格...")

        state = orchestrator.run(
            module_dir=module_dir,
            generate_only=False,
        )

        progress_bar.progress(100)
        logging.info(f"工作流完成，阶段: {state.stage.value}")

        # Write summary to history
        history_file = LOGS_DIR / "history.txt"
        with open(history_file, "a", encoding="utf-8") as f:
            f.write(f"[{timestamp}] {module_name}: {state.stage.value} - {user_input[:50]}...\n")

        # Display results
        with progress_container:
            st.success("✅ 生成完成！")
            st.info(f"📝 日志已保存到: {log_file}")

        # Read log to display LLM responses
        log_content = log_file.read_text(encoding="utf-8") if log_file.exists() else ""

        # Extract LLM responses from log
        spec_response = ""
        rtl_response = ""
        tb_response = ""

        lines = log_content.split('\n')
        capture_mode = None
        capture_content = []

        for line in lines:
            if "[Spec] 收到LLM响应" in line:
                capture_mode = "spec"
                capture_content = []
            elif "[RTL] 收到LLM响应" in line:
                if capture_mode == "spec" and capture_content:
                    spec_response = '\n'.join(capture_content)
                capture_mode = "rtl"
                capture_content = []
            elif "[TB] 收到LLM响应" in line:
                if capture_mode == "rtl" and capture_content:
                    rtl_response = '\n'.join(capture_content)
                capture_mode = "tb"
                capture_content = []
            elif capture_mode and "发送Prompt" not in line:
                capture_content.append(line)

        if capture_mode == "tb" and capture_content:
            tb_response = '\n'.join(capture_content)

        # Results section
        st.divider()
        st.header("📊 结果")

        # Display LLM responses
        if spec_response or rtl_response or tb_response:
            st.subheader("🤖 LLM响应")

            if spec_response:
                with st.expander("📋 规格分析响应", expanded=False):
                    try:
                        import json
                        spec_data = json.loads(spec_response)
                        st.json(spec_data)
                    except:
                        st.code(spec_response)

            if rtl_response:
                with st.expander("🔧 RTL生成响应", expanded=False):
                    st.code(rtl_response, language="verilog")

            if tb_response:
                with st.expander("🧪 Testbench生成响应", expanded=False):
                    st.code(tb_response, language="verilog")

        # Stage info
        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("模块名称", state.module_name)
        with col2:
            stage_emoji = {
                Stage.DONE: "✅",
                Stage.SIM_PASSED: "✅",
                Stage.FAILED: "❌",
                Stage.RTL_READY: "🔧",
                Stage.TB_READY: "🔧",
                Stage.NEEDS_CLARIFICATION: "❓",
            }
            emoji = stage_emoji.get(state.stage, "⏳")
            st.metric("当前阶段", f"{emoji} {state.stage.value}")
        with col3:
            attempts = sum(state.attempts.values())
            st.metric("总尝试次数", attempts)

        # Show notes/warnings
        if state.notes:
            with st.expander("📝 过程记录", expanded=False):
                for note in state.notes:
                    st.write(f"- {note}")

        # RTL Code
        if state.current_rtl:
            st.subheader("📄 生成的RTL代码")
            rtl_path = Path(state.current_rtl.filepath)
            if rtl_path.exists():
                rtl_code = rtl_path.read_text(encoding="utf-8")
                st.code(rtl_code, language="verilog")

                # Download button
                st.download_button(
                    label="💾 下载RTL文件",
                    data=rtl_code,
                    file_name=f"{state.module_name}.v",
                    mime="text/plain"
                )
            else:
                st.warning("RTL文件未生成")

        # Testbench
        if state.current_tb:
            with st.expander("🧪 测试台代码"):
                tb_path = Path(state.current_tb.filepath)
                if tb_path.exists():
                    tb_code = tb_path.read_text(encoding="utf-8")
                    st.code(tb_code, language="verilog")

        # Simulation results
        if state.sim_report:
            st.subheader("🎯 仿真结果")
            col1, col2 = st.columns(2)
            with col1:
                if state.sim_report.passed:
                    st.success("✅ 仿真通过")
                else:
                    st.error("❌ 仿真失败")
            with col2:
                failure_count = len(state.sim_report.failures)
                st.metric("失败数量", failure_count)

            if state.sim_report.run_log:
                with st.expander("📜 仿真日志"):
                    st.text(state.sim_report.run_log)

            if state.sim_report.failures:
                with st.expander("❌ 失败详情"):
                    for i, failure in enumerate(state.sim_report.failures):
                        st.write(f"**失败 {i+1}**: {failure.category.value}")
                        st.write(f"  {failure.message}")

        # Synthesis results
        if state.synth_report:
            st.subheader("🔨 综合结果")
            col1, col2, col3 = st.columns(3)
            with col1:
                if state.synth_report.passed:
                    st.success("✅ 综合通过")
                else:
                    st.error("❌ 综合失败")
            with col2:
                cell_count = state.synth_report.cell_count
                st.metric("单元数量", cell_count if cell_count else "N/A")
            with col3:
                cp = state.synth_report.critical_path_ns
                st.metric("关键路径(ns)", f"{cp:.2f}" if cp else "N/A")

            if state.synth_report.warnings:
                with st.expander("⚠️ 综合警告"):
                    for w in state.synth_report.warnings:
                        st.write(f"- {w}")

        # Show clarifications if needed
        if state.stage == Stage.NEEDS_CLARIFICATION and state.clarifications:
            st.warning("⚠️ 需要澄清以下问题:")
            for cl in state.clarifications:
                st.write(f"- **{cl.field}**: {cl.question}")

    except Exception as e:
        import traceback
        logging.error(f"错误: {str(e)}")
        logging.error(traceback.format_exc())
        with progress_container:
            st.error(f"❌ 错误: {str(e)}")
        with st.expander("详细错误信息"):
            st.code(traceback.format_exc())

# History section
st.divider()
st.header("📜 历史记录")

if ARTIFACTS_DIR.exists():
    modules = sorted([d for d in ARTIFACTS_DIR.iterdir() if d.is_dir()], key=lambda x: x.stat().st_mtime, reverse=True)

    if modules:
        for module_dir in modules[:10]:  # Show last 10
            state_path = module_dir / "workflow_state.json"
            if state_path.exists():
                import json
                state_data = json.loads(state_path.read_text())
                with st.expander(f"📁 {module_dir.name} - {state_data.get('stage', 'unknown')}"):
                    st.write(f"模块: {state_data.get('module_name', 'N/A')}")
                    st.write(f"阶段: {state_data.get('stage', 'N/A')}")
                    rtl_meta = module_dir / "rtl_meta.json"
                    if rtl_meta.exists():
                        st.json(rtl_meta.read_text())
    else:
        st.info("暂无生成历史")
