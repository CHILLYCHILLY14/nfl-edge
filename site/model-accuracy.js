/* Read-only prediction accuracy. Does not read or write any wager ledger. */
(function (root) {
  "use strict";
  const esc = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const num = (v, n=2) => v == null || !Number.isFinite(Number(v)) ? "—" : Number(v).toFixed(n);
  const pct = v => v == null ? "—" : `${num(v*100,1)}%`;
  const caches = new Map();
  function groupTable(title, groups) {
    return `<h3>${esc(title)}</h3><div class="ma-scroll"><table><thead><tr><th>Group</th><th>Logged</th><th>W–L–P</th><th>Pending</th><th>Win %</th><th>Units</th><th>ROI</th><th>Brier</th></tr></thead><tbody>${Object.entries(groups||{}).map(([k,v])=>`<tr><td>${esc(k)}</td><td>${v.logged}</td><td>${v.wins}–${v.losses}–${v.pushes}</td><td>${v.pending}</td><td>${pct(v.win_rate)}</td><td>${num(v.units)}</td><td>${pct(v.roi)}</td><td>${num(v.brier,3)}</td></tr>`).join("") || '<tr><td colspan="8">No predictions recorded yet.</td></tr>'}</tbody></table></div>`;
  }
  function render(host, data) {
    const o=data.overall||{}, g=data.games||{}, rows=data.records||[];
    const props=Object.entries(data.props||{});
    host.classList.add("model-accuracy");
    host.innerHTML=`<style>.model-accuracy{color:inherit;margin:18px 0}.model-accuracy p{line-height:1.6}.model-accuracy h3{margin:22px 0 10px}.ma-grid{display:flex;gap:12px;flex-wrap:wrap}.ma-stat{padding:12px 16px;border:1px solid #8885;border-radius:8px;min-width:130px}.ma-stat b{display:block;font-size:23px}.ma-scroll{overflow:auto;max-height:580px;border:1px solid #8885;border-radius:8px}.model-accuracy table{border-collapse:collapse;width:100%;font-size:13px;min-width:680px}.model-accuracy th,.model-accuracy td{text-align:left;padding:10px;border-bottom:1px solid #8883;white-space:normal}.model-accuracy th{font-weight:700}.ma-controls{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin:12px 0}.model-accuracy select,.model-accuracy input,.model-accuracy button{font:inherit;color:inherit;background:transparent;border:1px solid #8888;border-radius:5px;padding:7px}.model-accuracy option{color:#222;background:#fff}.model-accuracy small{opacity:.75}.ma-ok{color:#299c69}.ma-loss{color:#d45c55}</style>
      <h2>${esc(data.label||"Model accuracy")}</h2><p>${esc(data.method)}</p>
      <p><small>Updated ${esc(data.generated_at)} · <a href="data/accuracy.json" download>Download full prediction history</a>. Win rates exclude pushes and voids. Brier is probability error; lower is better. Tier versions are kept separate below.</small></p>
      <div class="ma-grid"><div class="ma-stat">Model picks logged<b>${o.logged||0}</b>${o.pending||0} pending</div><div class="ma-stat">Pick record<b>${o.wins||0}–${o.losses||0}–${o.pushes||0}</b>${o.voids||0} void · ${pct(o.win_rate)} wins</div><div class="ma-stat">Flat one-unit result<b>${num(o.units)}</b>${pct(o.roi)} hypothetical ROI</div><div class="ma-stat">Pick Brier<b>${num(o.brier,3)}</b>All selected tiers</div></div>
      ${g.logged ? `<h3>Every game forecast, with or without a bet</h3><div class="ma-grid"><div class="ma-stat">Games<b>${g.graded||0} graded</b>${g.pending||0} pending / ${g.logged} logged</div><div class="ma-stat">Winner accuracy<b>${pct(g.winner?.accuracy)}</b>${g.winner?.correct||0} / ${g.winner?.n||0} decided</div><div class="ma-stat">Spread direction<b>${pct(g.ats?.accuracy)}</b>${g.ats?.n||0} decided</div><div class="ma-stat">Total direction<b>${pct(g.totals?.accuracy)}</b>${g.totals?.n||0} decided</div><div class="ma-stat">Margin / total MAE<b>${num(g.margin_mae,1)} / ${num(g.total_mae,1)}</b>Points of absolute error</div><div class="ma-stat">Margin: model / market<b>${num(g.margin_comparison?.model_mae,1)} / ${num(g.margin_comparison?.market_mae,1)}</b>${g.margin_comparison?.n||0} matched games</div><div class="ma-stat">Total: model / market<b>${num(g.total_comparison?.model_mae,1)} / ${num(g.total_comparison?.market_mae,1)}</b>${g.total_comparison?.n||0} matched games</div></div>`:""}
      ${props.length ? `<h3>Player projections, including unpriced markets</h3><div class="ma-scroll"><table><thead><tr><th>Market</th><th>Logged</th><th>Graded</th><th>Mean absolute error</th><th>Bias (forecast − actual)</th></tr></thead><tbody>${props.map(([k,v])=>`<tr><td>${esc(k)}</td><td>${v.logged}</td><td>${v.graded}</td><td>${num(v.mae)}</td><td>${num(v.bias)}</td></tr>`).join("")}</tbody></table></div>`:""}
      ${!o.settled&&!g.graded&&!props.some(([,v])=>v.graded) ? '<p>Predictions are being saved before games start. Results appear after final scores or final player statistics arrive. Past games without a saved pregame forecast are not counted.</p>':""}
      ${groupTable("Accuracy by original tier",data.by_tier)}${groupTable("Accuracy by market",data.by_market)}
      <details><summary>Probability calibration and tier versions</summary>${groupTable("Tier calculation version",data.by_version)}<div class="ma-scroll"><table><thead><tr><th>Probability bucket</th><th>Sample</th><th>Mean prediction</th><th>Actual win rate</th></tr></thead><tbody>${(data.calibration||[]).map(c=>`<tr><td>${esc(c.bucket)}</td><td>${c.n}</td><td>${pct(c.predicted)}</td><td>${pct(c.actual)}</td></tr>`).join("")||'<tr><td colspan="4">Waiting for decided picks.</td></tr>'}</tbody></table></div></details>
      <h3>Prediction history</h3><p>Game forecasts and preferred market picks are shown by default. The complete audit also includes the opposite priced sides; its combined win rate is not a measure of pick quality.</p>
      <div class="ma-controls"><label>Show <select data-ma-kind><option value="preferred">Forecasts + preferred picks</option value="all">Complete priced-side audit</option value="game">Game forecasts</option><option value="prop">Player forecasts</option><option value="call">Preferred market picks</option></select></label><label>Result <select data-ma-result><option value="all">All</option>${["Pending","Win","Loss","Push","Void","Graded"].map(x=>`<option>${x}</option>`).join("")}</select></label><label>Tier <select data-ma-tier><option value="all">All</option>${[...new Set(rows.map(r=>r.tier).filter(Boolean))].sort().map(x=>`<option>${esc(x)}</option>`).join("")}</select></label><label>Search <input data-ma-search placeholder="Team, player or date" type="search"></label></div>
      <div class="ma-scroll"><table><thead><tr><th>Start / saved</th><th>Forecast or pick</th><th>Original tier</th><th>Prediction</th><th>Actual</th><th>Result</th></tr></thead><tbody data-ma-rows></tbody></table></div><div class="ma-controls"><button data-ma-prev>Previous</button><span data-ma-page></span><button data-ma-next>Next</button></div>`;
    let page=0;
    const q=s=>host.querySelector(s);
    function draw(){
      const kind=q("[data-ma-kind]").value, result=q("[data-ma-result]").value, tier=q("[data-ma-tier]").value, search=q("[data-ma-search]").value.toLowerCase();
      const filtered=rows.filter(r=>(kind==="all"||kind==="preferred"?(kind==="all"||r.kind!=="call"||r.selected):r.kind===kind&&(kind!=="call"||r.selected))&&(result==="all"||r.result===result)&&(tier==="all"||r.tier===tier)&&`${r.matchup} ${r.player||""} ${r.pick||""} ${r.start}`.toLowerCase().includes(search));
      page=Math.min(page,Math.max(0,Math.ceil(filtered.length/50)-1));
      q("[data-ma-rows]").innerHTML=filtered.slice(page*50,page*50+50).map(r=>{
        const prediction=r.kind==="game"?`Home margin ${num(r.margin,1)} · total ${num(r.total,1)}`:r.kind==="prop"?num(r.projection):`${pct(r.probability)} · ${r.price>0?"+":""}${num(r.price,0)}${r.line==null?"":` · line ${num(r.line,1)}`}`;
        const actual=r.actual!=null?num(r.actual):r.final_home!=null?`Away ${num(r.final_away,0)} – home ${num(r.final_home,0)}`:"—";
        return `<tr><td>${esc(r.start)}<br><small>Saved ${esc(r.captured_at)}</small></td><td>${esc(r.matchup)}<br>${esc(r.pick||[r.player,r.market].filter(Boolean).join(" · ")||"Game forecast")}</td><td>${esc(r.tier||"Forecast")}<br><small>${esc(r.version)}</small></td><td>${esc(prediction)}</td><td>${esc(actual)}</td><td class="${r.result==="Win"?"ma-ok":r.result==="Loss"?"ma-loss":""}">${esc(r.result)}${r.units==null?"":`<br>${num(r.units)} units`}</td></tr>`;
      }).join("")||'<tr><td colspan="6">No matching predictions.</td></tr>';
      q("[data-ma-page]").textContent=`${filtered.length} records · page ${page+1} of ${Math.max(1,Math.ceil(filtered.length/50))}`;
      q("[data-ma-prev]").disabled=page===0; q("[data-ma-next]").disabled=(page+1)*50>=filtered.length;
    }
    host.querySelectorAll("select,input").forEach(e=>e.addEventListener("input",()=>{page=0;draw();}));
    q("[data-ma-prev]").onclick=()=>{page--;draw();};q("[data-ma-next]").onclick=()=>{page++;draw();};draw();
  }
  let routed = false;
  function scan(){
    if (!routed && new URLSearchParams(location.search).get("tab") === "accuracy") {
      const button = [...document.querySelectorAll("button")].find(b=>/^Accuracy/.test(b.textContent.trim()));
      if (button) { routed = true; button.click(); }
    }
    document.querySelectorAll("[data-model-accuracy]").forEach(host=>{
      if(host.dataset.accuracyMounted)return;
      host.dataset.accuracyMounted="1";host.textContent="Loading prediction history…";
      const url=host.dataset.modelAccuracy||"data/accuracy.json";
      if(!caches.has(url))caches.set(url,fetch(url,{cache:"no-cache"}).then(r=>{if(!r.ok)throw new Error("unavailable");return r.json();}));
      caches.get(url).then(d=>render(host,d)).catch(()=>{host.textContent="Prediction history is unavailable. Check the latest refresh status and reload.";});
    });
  }
  if(typeof document!=="undefined"){
    if(document.readyState==="loading")document.addEventListener("DOMContentLoaded",scan);else scan();
    new MutationObserver(scan).observe(document.documentElement,{childList:true,subtree:true});
  }
  if(typeof module!=="undefined")module.exports={esc,num,pct};
})(typeof globalThis!=="undefined"?globalThis:this);
