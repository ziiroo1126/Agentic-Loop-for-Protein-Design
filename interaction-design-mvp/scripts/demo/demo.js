(() => {
  "use strict";
  const $ = id => document.getElementById(id);
  const data = JSON.parse($("demo-data").textContent);
  function chapter(name) {
    if (!document.querySelector(`.chapter[id="${name}"]`)) name = "intake";
    document.querySelectorAll(".chapter").forEach(section => {section.hidden = section.id !== name;});
    document.querySelectorAll("nav button").forEach(button => {
      if (button.dataset.panel === name) button.setAttribute("aria-current", "step");
      else button.removeAttribute("aria-current");
    });
    const frame = $(name === "structure" ? "structure-frame" : name === "adaptive" ? "adaptive-frame" : "");
    if (frame && !frame.hasAttribute("src")) frame.src = frame.dataset.src;
  }
  document.querySelectorAll("nav button").forEach(button => button.addEventListener("click", () => chapter(button.dataset.panel)));
  document.querySelectorAll("[data-next]").forEach(button => button.addEventListener("click", () => {chapter(button.dataset.next); document.querySelector("nav").scrollIntoView({block:"start"});}));
  const issueNames = {
    "target.reference_structure": "目标结构文件：本地 PDB 或 mmCIF",
    "target.segments": "目标区域：采用哪些链和残基区间",
    "target.hotspots": "热点：当前后端需要明确的目标残基",
    "binder.length": "binder 长度：先提供长度或长度偏好"
  };
  $("intake-status").textContent = `${data.intake_review.status} · ${data.intake_review.issues.length} 项待补齐`;
  data.intake_review.issues.forEach(issue => {
    const li = document.createElement("li"), title = document.createElement("strong"), detail = document.createElement("small");
    title.textContent = issueNames[issue.field] || issue.field; detail.textContent = issue.field;
    li.append(title,detail); $("issues").append(li);
  });
  $("task-json").textContent = JSON.stringify(data.task,null,2);
  $("show-task").addEventListener("click", () => {
    $("task-details").hidden = !$("task-details").hidden;
    $("show-task").textContent = $("task-details").hidden ? "查看本例已备齐的任务" : "收起本例任务";
  });
  $("pipeline-name").textContent = data.task.name;
  const steps = data.trace.steps;
  $("step").max = steps.length;
  const fmt = value => typeof value === "number" ? value.toFixed(3) : "—";
  function render() {
    const n = Number($("step").value), previous = n ? steps[n-1] : null;
    const state = steps[n]?.request?.payload?.visible_state;
    const candidates = state?.candidates || data.candidates.map(row => ({...row,evaluated:row.complex_evaluation_status === "evaluated"}));
    $("position").textContent = `${n} / ${steps.length}`;
    $("prev").disabled = n === 0; $("next").disabled = n === steps.length;
    $("candidate-count").textContent = candidates.length;
    $("evaluated-count").textContent = candidates.filter(c => c.evaluated).length;
    $("run-status").textContent = n === steps.length ? "已结束" : n === 0 ? "等待选择" : "已有反馈";
    $("candidates").replaceChildren();
    candidates.forEach(candidate => {
      const tr = document.createElement("tr"); if (candidate.evaluated) tr.className = "evaluated";
      const values = [`Seed ${candidate.seed}`, fmt(candidate.pre?.monomer_plddt), fmt(candidate.pre?.monomer_design_rmsd), candidate.evaluated ? "已评估" : "未评估", candidate.evaluated ? fmt(candidate.post?.iptm) : "—"];
      values.forEach(value => {const td=document.createElement("td");td.textContent=value;tr.append(td);});
      tr.title = candidate.candidate_id; $("candidates").append(tr);
    });
    const decision = previous?.submission?.decision;
    $("action-title").textContent = !decision ? "单体检查完成，等待宿主选择" : decision.action === "stop" ? "执行器已停止" : "已完成一次候选选择与复合物评估";
    $("reason").textContent = decision?.reason || "此时尚未选择候选，复合物预测结果不可用于决策。";
    const chosen = (decision?.candidate_ids || []).map(id => data.candidates.find(c=>c.candidate_id===id)).filter(Boolean);
    $("action-summary").textContent = !decision ? "Agent 收到候选列表、单体指标和剩余评估配额，需要引用已知证据决定下一步。" : decision.action === "stop" ? `保存的停止原因：${decision.reason}。` : `记录中的宿主选择了 ${chosen.map(c=>`Seed ${c.seed}`).join("、")}，工具返回了已保存的复合物评估结果。`;
    $("interpretation").textContent = !decision ? "当前表格只展示当时可见的检查结果。点击下一步，查看保存的选择与新反馈。" : decision.action === "stop" ? "本轮流程已经完成，可以导出序列、结构和证据。未被评估的候选仍然没有复合物分数。" : "新的结构预测提供了额外证据。Agent 的选择可能涉及多个指标之间的取舍，完整理由保存在原始决策轨迹中。";
  }
  $("step").addEventListener("input",render);
  $("prev").addEventListener("click",()=>{$("step").value=Number($("step").value)-1;render();});
  $("next").addEventListener("click",()=>{$("step").value=Number($("step").value)+1;render();});
  render(); chapter("intake");
})();
