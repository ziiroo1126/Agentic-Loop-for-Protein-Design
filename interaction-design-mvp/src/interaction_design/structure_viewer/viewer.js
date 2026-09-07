/* ALPD offline structure browser. No network or model calls. */
(() => {
  "use strict";
  const $ = id => document.getElementById(id);
  const data = JSON.parse($("structure-data").textContent);
  const palette = ["#327bbb", "#e58a35", "#35a38b", "#ac72ba", "#ce677d", "#92913a", "#57a6bb"];
  const kinds = {
    generated_structure: ["生成复合物", "生成阶段的设计结构。"],
    monomer_structure: ["单体预测", "单独预测的 binder，不包含目标蛋白。"],
    predicted_complex: ["复合物预测", "复合物评估阶段保存的预测结构。"],
    reference: ["目标参考结构", "任务提供的原始参考结构，可能包含未参与设计的链。"]
  };
  let viewer = null, current = null, atoms = [], chainState = new Map(), spinning = false;
  const row = () => data.candidates[Number($("candidate").value)];
  function message(text) { $("message").textContent = text; $("message").hidden = !text; }
  function download(url, name) {
    const link = document.createElement("a"); link.href = url; link.download = name;
    document.body.append(link); link.click(); link.remove();
  }
  function style() {
    if (!viewer || !atoms.length) return;
    viewer.setStyle({}, {});
    const representation = $("style").value;
    for (const [chain, state] of chainState) {
      if (!state.visible) continue;
      const spec = {[representation]: {color: state.color}};
      // Non-polymer atoms remain visible even in cartoon mode.
      viewer.setStyle({chain}, spec);
      if (representation === "cartoon") viewer.setStyle({chain, hetflag: true}, {stick: {color: state.color, radius: 0.15}});
    }
    viewer.render();
  }
  function reset() {
    if (!viewer || !atoms.length) return;
    const chains = [...chainState].filter(([,s]) => s.visible).map(([c]) => c);
    if (chains.length) { viewer.zoomTo({chain: chains}); viewer.render(); }
  }
  function load() {
    const candidate = row(), kind = $("structure").value;
    const key = kind === "reference" ? data.reference : candidate?.structures[kind];
    current = key ? data.structures[key] : null;
    $("source-note").textContent = kinds[kind]?.[1] || "";
    $("download").disabled = !current;
    $("residue").textContent = "尚未选择残基";
    $("chains").replaceChildren();
    $("atom-count").textContent = "—";
    atoms = []; chainState = new Map();
    for (const id of ["reset", "spin", "png"]) $(id).disabled = true;
    if (!viewer) return;
    viewer.spin(false); spinning = false; $("spin").setAttribute("aria-pressed", "false");
    viewer.removeAllModels(); viewer.removeAllLabels(); viewer.render();
    if (!current) { message("没有可浏览的结构。"); return; }
    try {
      const model = viewer.addModel(current.text, current.format, {keepH: false});
      atoms = model.selectedAtoms({});
      if (!atoms.length || atoms.some(a => ![a.x,a.y,a.z].every(Number.isFinite))) throw new Error("未解析到有效原子坐标");
      const chains = [...new Set(atoms.map(a => a.chain || ""))].sort();
      chains.forEach((chain, index) => {
        const color = palette[index % palette.length];
        chainState.set(chain, {color, visible: true});
        const label = document.createElement("label"); label.className = "chain";
        const check = document.createElement("input"); check.type = "checkbox"; check.checked = true;
        check.addEventListener("change", () => {chainState.get(chain).visible = check.checked; viewer.removeAllLabels(); style();});
        const swatch = document.createElement("span"); swatch.className = "swatch"; swatch.style.backgroundColor = color;
        label.append(check, swatch, document.createTextNode(`链 ${chain || "(空)"}`)); $("chains").append(label);
      });
      viewer.setClickable({}, true, atom => {
        if (!chainState.get(atom.chain || "")?.visible) return;
        const text = `链 ${atom.chain || "(空)"} · ${atom.resn} ${atom.resi}${atom.icode?.trim() || ""} · ${atom.atom}`;
        $("residue").textContent = text;
        viewer.removeAllLabels();
        viewer.addLabel(text, {position: atom, fontSize: 12, backgroundColor: "#23374b", backgroundOpacity: 0.85});
        viewer.render();
      });
      style(); reset(); message("");
      $("atom-count").textContent = `${atoms.length.toLocaleString()} 个原子 · ${chains.length} 条链`;
      for (const id of ["reset", "spin", "png"]) $(id).disabled = false;
    } catch (error) {
      viewer.removeAllModels(); viewer.render(); atoms = []; $("chains").replaceChildren();
      message(`无法显示此结构：${error.message}。可下载原文件检查，或切换其他结构。`);
    }
  }
  function metrics(candidate) {
    const fields = [
      ["单体 pLDDT", candidate?.pre?.monomer_plddt, "", false],
      ["单体 / 设计 RMSD", candidate?.pre?.monomer_design_rmsd, " Å", false],
      ["生成热点覆盖率", candidate?.pre?.generated_hotspot_coverage, "%", false],
      ["生成冲突残基对", candidate?.pre?.generated_clash_residue_pairs, " 对", false],
      ["复合物 ipTM", candidate?.post?.iptm, "", true],
      ["预测热点覆盖率", candidate?.post?.hotspot_coverage, "%", true],
      ["预测冲突残基对", candidate?.post?.clash_residue_pairs, " 对", true],
      ["目标对齐后 binder RMSD", candidate?.post?.binder_rmsd_after_target_alignment, " Å", true]
    ];
    $("metrics").replaceChildren();
    fields.forEach(([label, value, unit, complex]) => {
      const dl = document.createElement("dl"), dt = document.createElement("dt"), dd = document.createElement("dd");
      dl.className = "metric"; dt.textContent = label;
      if (typeof value === "number" && Number.isFinite(value)) {
        const number = unit === "%" ? value * 100 : value;
        dd.textContent = number.toLocaleString(undefined, {maximumFractionDigits: unit === " 对" ? 0 : 2}) + unit;
      } else {dd.textContent = complex && candidate && !candidate.evaluated ? "未评估" : "无记录"; dd.className = "missing";}
      dl.append(dt, dd); $("metrics").append(dl);
    });
  }
  function selectCandidate() {
    const candidate = row(), previous = $("structure").value;
    $("candidate-status").textContent = candidate ? `Seed ${candidate.seed} · ${candidate.length} aa · 复合物${candidate.evaluated ? "已评估" : "未评估"}` : "结果包没有候选";
    $("sequence").textContent = candidate?.sequence || "无记录";
    metrics(candidate); $("structure").replaceChildren();
    for (const [key, [label]] of Object.entries(kinds)) {
      const available = key === "reference" ? data.reference : candidate?.structures[key];
      const option = new Option(label + (available ? "" : key === "predicted_complex" && !candidate?.evaluated ? "（未评估）" : "（无文件）"), key);
      option.disabled = !available; $("structure").append(option);
    }
    const available = [...$("structure").options].filter(o => !o.disabled);
    $("structure").value = available.some(o => o.value === previous) ? previous : (available[0]?.value || "");
    $("structure").disabled = available.length === 0;
    load();
  }
  $("task-name").textContent = data.name;
  data.candidates.forEach((candidate, index) => $("candidate").append(new Option(candidate.candidate_id, String(index))));
  $("candidate").disabled = !data.candidates.length;
  try { viewer = $3Dmol.createViewer($("viewer"), {backgroundColor: "#f9fbfd", antialias: true}); }
  catch (_) { message("三维浏览器无法启动。请使用支持 WebGL 的浏览器，并检查硬件加速设置。指标与结构下载仍可使用。"); }
  $("candidate").addEventListener("change", selectCandidate);
  $("structure").addEventListener("change", load);
  $("style").addEventListener("change", style);
  $("reset").addEventListener("click", reset);
  $("spin").addEventListener("click", () => {spinning = !spinning; viewer.spin(spinning ? "y" : false); $("spin").setAttribute("aria-pressed", String(spinning));});
  $("png").addEventListener("click", () => {viewer.render(); download(viewer.pngURI(), "alpd-structure.png");});
  $("download").addEventListener("click", () => {
    if (!current) return;
    const url = URL.createObjectURL(new Blob([current.text], {type: "text/plain;charset=utf-8"}));
    download(url, `${$("structure").value}.${current.format}`);
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
  new ResizeObserver(() => {if (viewer) viewer.resize();}).observe($("viewport"));
  selectCandidate();
})();
