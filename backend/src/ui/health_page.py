"""Streamlit health-check views for production readiness."""

from __future__ import annotations

from typing import Any, Mapping

import streamlit as st

from src.runtime.health import CHECK_FAIL, CHECK_PASS, CHECK_SKIP, CHECK_WARN, health_summary


STATUS_LABELS = {
    CHECK_PASS: "通过",
    CHECK_WARN: "警告",
    CHECK_FAIL: "失败",
    CHECK_SKIP: "跳过",
}


def render_sidebar_health_status(health: Mapping[str, Any] | None) -> None:
    """Render a compact health summary in the sidebar."""
    if not health:
        return
    summary = health_summary(health)
    if summary["ready"]:
        st.success("启动健康检查通过")
    else:
        st.error("启动健康检查失败")
    st.caption(
        f"后端：{summary.get('backend') or '-'}；"
        f"通过 {summary['pass_count']}，警告 {summary['warn_count']}，失败 {summary['fail_count']}"
    )
    if health.get("cached"):
        st.caption("结果来自缓存；配置、模型或依赖变化后会自动重新检查。")


def render_health_banner(health: Mapping[str, Any] | None) -> None:
    """Render a top-level health banner."""
    if not health:
        return
    if health.get("ready"):
        st.success("生产启动健康检查通过，真实 OCSR 工作流可用。")
        return
    st.warning("生产启动健康检查未通过：图片、文档和批量识别已禁用，SMILES 手动分析仍可使用。")
    suggestions = list(health.get("repair_suggestions") or [])
    if suggestions:
        with st.expander("修复建议", expanded=True):
            for suggestion in suggestions:
                st.write(f"- {suggestion}")


def render_blocked_workflow(health: Mapping[str, Any] | None, workflow_name: str) -> None:
    """Explain why a production OCSR workflow is disabled."""
    st.error(f"{workflow_name}暂不可用：生产启动健康检查未通过。")
    if health:
        _render_failed_checks(health)
        suggestions = list(health.get("repair_suggestions") or [])
        if suggestions:
            st.subheader("修复建议")
            for suggestion in suggestions:
                st.write(f"- {suggestion}")
    st.info("SMILES 手动分析不依赖图片识别模型，仍可以继续使用。")


def render_health_page(health: Mapping[str, Any] | None) -> None:
    """Render the full health-check page."""
    st.subheader("生产启动健康检查")
    if st.button("执行完整模型预热", key="force_health_check"):
        st.session_state["health_force_refresh"] = True
        st.session_state["health_full_warmup"] = True
        st.rerun()
    if not health:
        st.info("尚未生成健康检查结果。")
        return

    summary = health_summary(health)
    metrics = st.columns(4)
    metrics[0].metric("状态", "通过" if summary["ready"] else "失败")
    metrics[1].metric("通过", summary["pass_count"])
    metrics[2].metric("警告", summary["warn_count"])
    metrics[3].metric("失败", summary["fail_count"])
    st.caption(
        f"后端：{health.get('backend')}；"
        f"耗时：{health.get('duration_ms')} ms；"
        f"缓存：{'是' if health.get('cached') else '否'}；"
        f"生成时间：{health.get('created_at')}"
    )
    _render_run_cleanup_summary(st.session_state.get("run_cleanup"))

    st.subheader("检查项")
    for check in health.get("checks") or []:
        _render_check(check)

    suggestions = list(health.get("repair_suggestions") or [])
    if suggestions:
        st.subheader("修复建议")
        for suggestion in suggestions:
            st.write(f"- {suggestion}")

    with st.expander("缓存与追踪信息", expanded=False):
        st.json(
            {
                "cache_key": health.get("cache_key"),
                "backend": health.get("backend"),
                "runtime_config": health.get("runtime_config"),
                "model_path": health.get("model_path"),
                "model_sha256": health.get("model_sha256"),
                "model_fingerprint": health.get("model_fingerprint"),
                "config_warnings": health.get("config_warnings"),
                "worker": health.get("worker"),
                "dependency_versions": health.get("dependency_versions"),
                "git_commit": health.get("git_commit"),
                "capabilities": health.get("capabilities"),
            }
        )
    with st.expander("后端原始诊断", expanded=False):
        st.json(health.get("backend_status") or {})


def _render_failed_checks(health: Mapping[str, Any]) -> None:
    failed = [item for item in health.get("checks") or [] if item.get("status") == CHECK_FAIL]
    if not failed:
        return
    st.subheader("失败项")
    for check in failed:
        st.write(f"- **{check.get('name')}**：{check.get('message')}")


def _render_run_cleanup_summary(cleanup: Mapping[str, Any] | None) -> None:
    if not cleanup:
        return
    status = str(cleanup.get("status") or "unknown")
    if status == "completed":
        message = (
            f"运行目录自动清理已执行；删除 {cleanup.get('deleted_count', 0)} 个，"
            f"释放 {cleanup.get('freed_bytes', 0)} 字节。"
        )
    elif status == "skipped":
        message = f"运行目录自动清理未到期；下次最早执行时间：{cleanup.get('next_run_after') or '-'}。"
    else:
        message = f"运行目录自动清理状态：{status}。"
    st.caption(message)
    with st.expander("运行目录自动清理", expanded=False):
        st.json(dict(cleanup))


def _render_check(check: Mapping[str, Any]) -> None:
    status = str(check.get("status") or "")
    label = STATUS_LABELS.get(status, status or "未知")
    title = f"{label} · {check.get('name')}"
    message = str(check.get("message") or "")
    if status == CHECK_PASS:
        st.success(f"{title}：{message}")
    elif status == CHECK_WARN:
        st.warning(f"{title}：{message}")
    elif status == CHECK_FAIL:
        st.error(f"{title}：{message}")
    else:
        st.info(f"{title}：{message}")
    details = check.get("details")
    if details:
        with st.expander(f"{check.get('name')} 详情", expanded=False):
            st.json(details)
