<script lang="ts">
 import {metric,powerLabel,timeLabel,hardwareFreshness,buildPsuRows,type SystemNode} from '$lib/utils/systemDashboard';
 import {temperatureTone,psuLoad,psuInputDistribution,telemetryDelta,gpuActivity,type ServerTelemetryHistory} from '$lib/utils/systemTelemetryVisuals';
 import SystemSparkline from './SystemSparkline.svelte';import SystemFanRotor from './SystemFanRotor.svelte';
 let {node,now=Date.now(),compact=true,history=undefined}:{node:SystemNode;now?:number;compact?:boolean;history?:ServerTelemetryHistory}=$props();
 let expanded=$state(false);
 $effect.pre(()=>{expanded=!compact;});
 const serverState=$derived(node.state);
 const activity=$derived(gpuActivity(serverState,now));
 const freshness=$derived(hardwareFreshness(node.hardware,now));
 const fresh=$derived(freshness==='fresh');
 const psus=$derived(buildPsuRows(node.psuInput,node.psuOutput,node.hardware?.psu_rated_output_watts));
 const inputDistribution=$derived(psuInputDistribution(psus));
 const serverFans=$derived(node.serverFans.filter(fan=>fan.value!==null));
 const gpuFans=$derived(node.gpuFans.filter(fan=>fan.value!==null));
 const gpuRpm=$derived(gpuFans.length?gpuFans.reduce((sum,fan)=>sum+fan.value!,0)/gpuFans.length:null);
 const cpuSensors=$derived((node.hardware?.sensors??[]).filter(sensor=>sensor.kind==='temperature'&&/cpu/i.test(sensor.name)&&!/dimm/i.test(sensor.name)));
 const cpuValues=$derived(cpuSensors.map(sensor=>sensor.value).filter((value):value is number=>value!==null&&Number.isFinite(value)));
 const gpuValues=$derived(serverState.gpus.map(gpu=>gpu.temperature).filter(value=>Number.isFinite(value)));
 const cpuAverage=$derived(cpuValues.length?cpuValues.reduce((sum,value)=>sum+value,0)/cpuValues.length:null);
 const gpuMaximum=$derived(gpuValues.length?Math.max(...gpuValues):null);
 const gpuAverage=$derived(gpuValues.length?gpuValues.reduce((sum,value)=>sum+value,0)/gpuValues.length:null);
 const cpuWarnings=$derived(cpuSensors.map(sensor=>sensor.upper_warning).filter((value):value is number=>value!==null&&value>0));
 const cpuWarning=$derived(cpuWarnings.length?Math.min(...cpuWarnings):75);
 const cpuCriticals=$derived(cpuSensors.map(sensor=>sensor.upper_critical).filter((value):value is number=>value!==null&&value>0));
 const cpuCritical=$derived(cpuCriticals.length?Math.min(...cpuCriticals):90);
 const network=$derived(serverState.system as (NonNullable<typeof serverState.system>&{network_rx_bytes_per_second?:number|null;network_tx_bytes_per_second?:number|null})|null);
 const rx=$derived(network?.network_rx_bytes_per_second);
 const tx=$derived(network?.network_tx_bytes_per_second);
 const delta=$derived(telemetryDelta(history?.power??[]));
 const kw=(value:number|null)=>value===null?'—':metric(value/1000,3);
 const rate=(value:number|null|undefined)=>typeof value==='number'&&Number.isFinite(value)?metric(value/1e6,3):'—';
 const percent=(value:number|null)=>Math.max(0,Math.min(100,value??0));
 function toggle(event:MouseEvent){if(window.getSelection()?.toString().trim())return;if(event.target instanceof Element&&event.target.closest('a,button,input,select,textarea,[contenteditable]'))return;expanded=!expanded;}
 function keyToggle(event:KeyboardEvent){if(event.target!==event.currentTarget||(event.key!=='Enter'&&event.key!==' '))return;event.preventDefault();expanded=!expanded;}
</script>

<div class="sys-server-row hardware-card" class:hardware-expanded={expanded} role="button" tabindex="0" aria-label={`${serverState.server_name} 서버`} aria-expanded={expanded} aria-controls={`system-details-${serverState.server_id}`} onclick={toggle} onkeydown={keyToggle}>
 <header class="hardware-heading"><div class="hardware-identity"><svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.4" aria-hidden="true"><rect x="3" y="3" width="18" height="7" rx="2"/><rect x="3" y="14" width="18" height="7" rx="2"/><path d="M6 6.5h1m3 0h7M6 17.5h1m3 0h7"/></svg><h3>{serverState.server_name}</h3><span>{serverState.host}</span><span class="hardware-status" class:attention={node.needsAttention}>{serverState.status==='offline'?'연결 끊김':serverState.status==='unknown'?'대기':node.needsAttention?'주의':'정상'}</span></div><div class="hardware-heading-end"><span class="hardware-activity" title="사용자 연결 · GPU 사용률 >5% · GPU 메모리 >10% 중 하나">GPU active <b>{activity.active===null?'—':activity.active}/{activity.total}</b></span>{#if freshness==='stale'||freshness==='snapshot'}<span>{freshness==='stale'?'갱신 지연':'보관 기록'}</span>{/if}<svg class:open={expanded} width="18" height="18" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><path d="m5 7 5 5 5-5"/></svg></div></header>
 <div class="hardware-zones">
  <section class="hardware-power"><h4 class="zone-label"><svg viewBox="0 0 20 20" aria-hidden="true"><path d="m11 1-7 10h5l-1 8 8-11h-5z"/></svg>전력 · PSU 부하 <small>서버 DCMI</small></h4><div class="hardware-main-value power-value">{kw(node.powerWatts)}<small> kW</small>{#if delta!==null}<span class="hardware-delta">{delta>=0?'+':''}{metric(delta/1000,3)} kW</span>{/if}</div><SystemSparkline series={[{points:history?.power??[],color:'var(--sys-power,#e7d969)'}]} {now} height={20} label="서버 전력 · 최근 실제 이력"/>
   <div class="hardware-psus" aria-label="PSU별 정격 출력 대비 부하">
    {#each psus as psu (psu.name)}
     {@const load=psuLoad(psu.outputWatts,psu.ratedOutputWatts)}
     <div class="hardware-psu" title={`출력 ${powerLabel(psu.outputWatts)} / 정격 ${powerLabel(psu.ratedOutputWatts)}`}>
      <span>{psu.name}</span>
      {#if load!==null}
       <span class="psu-load-track" aria-hidden="true" class:overload={load>1}><i style:width={`${percent(load*100)}%`}></i></span>
       <strong>{metric(load*100,1)}<small>%</small></strong>
      {:else if psu.outputWatts!==null}
       <strong>{kw(psu.outputWatts)}<small> kW</small></strong>
      {:else}
       <span class="psu-unavailable">출력 미측정</span>
      {/if}
     </div>
    {:else}<div class="hardware-empty">PSU —</div>{/each}
    {#if psus.some(psu=>psu.ratedOutputWatts===null)}
     <small class="psu-unavailable">정격 미등록 슬롯은 출력만 표시</small>
    {/if}
   </div>
  </section>
  <section class="hardware-thermal"><h4 class="zone-label"><svg viewBox="0 0 20 20" aria-hidden="true"><path d="M7 11V4a3 3 0 0 1 6 0v7a5 5 0 1 1-6 0Z"/><path d="M10 5v9m0 0a1.5 1.5 0 1 0 0 3 1.5 1.5 0 0 0 0-3Z"/></svg>온도 <small>최고 / 평균</small></h4>
   <div class="thermal-reading" data-tone={temperatureTone(node.cpuTemperature,cpuWarning,cpuCritical)}><span>CPU</span><strong>{metric(node.cpuTemperature)}<small>°C</small></strong><span class="thermal-average">평균 {metric(cpuAverage)}°</span><div class="thermal-track"><i style:width={`${percent(node.cpuTemperature)}%`}></i></div></div>
   <div class="thermal-reading gpu-thermal" data-tone={temperatureTone(gpuMaximum,80,90)}><span>GPU</span><strong>{metric(gpuMaximum)}<small>°C</small></strong><span class="thermal-average">평균 {metric(gpuAverage)}°</span><div class="thermal-track"><i style:width={`${percent(gpuMaximum)}%`}></i></div></div>
   <SystemSparkline series={[{points:history?.cpu_temperature??[],color:'var(--sys-temperature,#78a7df)'},{points:history?.gpu_temperature??[],color:'var(--sys-gpu-temperature,#e5a16b)'}]} {now} height={38} maximum={110} label="CPU와 GPU 최고 온도 · 최근 실제 이력"/>
  </section>
  <section class="hardware-cooling"><h4 class="zone-label"><svg viewBox="0 0 20 20" aria-hidden="true"><circle cx="10" cy="10" r="7.5"/><path d="m10 3 1 5 5-1-4 4 3 4-5-2-3 3 1-5-4-2 5-1z"/></svg>냉각 팬 <small>BMC RPM</small></h4>
   <div class="cooling-group"><SystemFanRotor rpm={node.fanRpm} {fresh} size={30} label="서버 팬 평균"/><div class="cooling-reading"><span>Server <span>Fan speed avg.</span></span><strong>{metric(node.fanRpm)} <small>RPM</small></strong></div><div class="cooling-rotors" style={`--fan-columns:${node.serverFans.length>4?Math.ceil(node.serverFans.length/2):Math.max(1,node.serverFans.length)}`} data-fan-count={node.serverFans.length}>{#each node.serverFans as fan}<SystemFanRotor rpm={fan.value} {fresh} size={16} label={fan.name}/>{/each}</div></div>
   <div class="cooling-group"><SystemFanRotor rpm={gpuRpm} {fresh} size={30} label="GPU 구역 섀시 팬 평균"/><div class="cooling-reading"><span>GPU bay <span>Fan speed avg.</span></span><strong>{metric(gpuRpm)} <small>RPM</small></strong></div><div class="cooling-rotors" style={`--fan-columns:${node.gpuFans.length>4?Math.ceil(node.gpuFans.length/2):Math.max(1,node.gpuFans.length)}`} data-fan-count={node.gpuFans.length}>{#each node.gpuFans as fan}<SystemFanRotor rpm={fan.value} {fresh} size={16} label={fan.name}/>{/each}</div></div>
  </section>
  <section class="hardware-network"><h4 class="zone-label"><svg viewBox="0 0 20 20" aria-hidden="true"><path d="M4 1v14m-3-3 3 3 3-3M16 19V5m-3 3 3-3 3 3"/></svg>네트워크 <small title="Linux 물리 NIC의 합계 · NAS 전용 NIC 포함">물리 NIC 합계 · MB/s</small></h4><div class="network-reading"><span class="rx-label">↓ RX</span><strong>{rate(rx)}</strong></div><div class="network-reading"><span class="tx-label">↑ TX</span><strong>{rate(tx)}</strong></div><SystemSparkline series={[{points:history?.network_rx??[],color:'var(--net-rx)'},{points:history?.network_tx??[],color:'var(--net-tx)'}]} {now} height={51} label="수신 RX와 송신 TX · 최근 실제 이력"/></section>
 </div>
 <div class="hardware-resource-strip"><div><span>CPU</span><span class="resource-track"><i style:width={`${percent(node.cpuPercent)}%`}></i></span><strong>{metric(node.cpuPercent)}%</strong></div><div><span>RAM</span><span class="resource-track"><i style:width={`${percent(node.ramPercent)}%`}></i></span><strong>{metric(node.ramPercent)}%</strong></div>{#if node.issues.length}<span class="hardware-issue">{node.issues[0]}</span>{/if}</div>
 <div id={`system-details-${serverState.server_id}`} hidden={!expanded} class="hardware-details"><section><h4 class="zone-label">PSU 입력 / 출력 · kW</h4><table><thead><tr><th>장치</th><th>입력</th><th>출력</th><th>정격</th><th>입력 이력</th></tr></thead><tbody>{#each psus as psu}<tr><th>{psu.name}</th><td>{kw(psu.inputWatts)}</td><td>{kw(psu.outputWatts)}</td><td>{kw(psu.ratedOutputWatts)}</td><td><SystemSparkline series={[{points:history?.psu?.[psu.name]??[],color:'var(--sys-power,#e7d969)'}]} {now} height={22} label={`${psu.name} 입력 전력 이력`}/></td></tr>{/each}</tbody></table>
   {#if inputDistribution.length}
    <div class="psu-distribution">
     <small>측정된 PSU 입력 분담</small>
     <div class="psu-distribution-track" aria-hidden="true">
      {#each inputDistribution as part, index (part.name)}
       <span class="psu-segment" style:--psu-color={`var(--psu-part-${index%4})`} style:width={`${part.ratio*100}%`} title={`${part.name} ${metric(part.ratio*100,1)}%`}></span>
      {/each}
     </div>
     <div class="psu-distribution-legend">
      {#each inputDistribution as part, index (part.name)}
       <span style:--psu-color={`var(--psu-part-${index%4})`}>{part.name} {metric(part.ratio*100,1)}%</span>
      {/each}
     </div>
    </div>
   {/if}</section><section><h4 class="zone-label">CPU / GPU 개별 온도</h4><div class="detail-devices">{#each cpuSensors as sensor}<span data-tone={temperatureTone(sensor.value,cpuWarning,cpuCritical)}><span>{sensor.name}</span><strong>{metric(sensor.value)}°C</strong></span>{/each}{#each serverState.gpus as gpu}<span data-device="gpu" data-tone={temperatureTone(gpu.temperature,80,90)}><span>GPU {gpu.index}</span><strong>{metric(gpu.temperature)}°C</strong></span>{/each}</div></section><section><h4 class="zone-label">개별 냉각 팬 · BMC RPM</h4><div class="detail-fans">{#each [...node.serverFans,...node.gpuFans] as fan}<span><SystemFanRotor rpm={fan.value} {fresh} size={22} label={fan.name}/><span>{fan.name}</span><strong>{metric(fan.value)}</strong></span>{/each}</div></section><footer>{timeLabel(node.hardware?.collected_at)}{#if node.issues.length} · {node.issues.join(' · ')}{/if}</footer></div>
</div>

<style>
 .hardware-card{--power:var(--sys-power,#e7d969);--thermal:var(--sys-temperature,#78a7df);--gpu:var(--sys-gpu-temperature,#e5a16b);--fan:var(--sys-fan,#9faebc);--net-rx:var(--sys-network-rx,#7597b5);--net-tx:var(--sys-network-tx,#89929c);--thermal-warning:var(--warning);--thermal-critical:var(--ops-danger);border-radius:12px;overflow:hidden;text-align:left;cursor:pointer}.hardware-card:focus-visible{outline:2px solid var(--ops-primary);outline-offset:3px}.hardware-heading{display:flex;align-items:center;justify-content:space-between;gap:16px;padding:10px 18px 8px;border-bottom:1px solid var(--ops-border)}.hardware-identity{display:flex;align-items:center;gap:12px;min-width:0}.hardware-identity>svg{color:var(--ops-muted-fg);flex-shrink:0}.hardware-identity h3{font-size:17px;line-height:1.2;font-weight:650;margin:0}.hardware-identity>span{font-size:12px;color:var(--ops-muted-fg)}.hardware-status{margin-left:3px}.hardware-status.attention{color:var(--warning)}.hardware-heading-end{display:flex;align-items:center;gap:12px;color:var(--ops-muted-fg);font-size:12px}.hardware-heading-end>svg{transition:transform .15s}.hardware-heading-end>svg.open{transform:rotate(180deg)}
 .hardware-activity{display:flex;align-items:baseline;gap:5px;font-size:13px;white-space:nowrap}.hardware-activity>b{font-size:16px;font-weight:550;color:var(--ops-fg);font-variant-numeric:tabular-nums}
 .hardware-zones{display:grid;grid-template-columns:1.15fr 1.15fr 1.25fr 1fr;padding:10px 18px;gap:22px}.hardware-card section{min-width:0}.hardware-card .zone-label{display:flex;align-items:center;gap:6px;font-size:13px;line-height:1.3;font-weight:450;color:var(--ops-muted-fg);margin:0 0 7px;white-space:nowrap}.zone-label>svg{width:16px;height:16px;fill:none;stroke:currentColor;stroke-width:1.4;flex-shrink:0}.zone-label>small{font-size:12px;margin-left:auto;font-weight:400}.hardware-power .zone-label>svg{color:var(--power)}.hardware-thermal .zone-label>svg{color:var(--thermal)}.hardware-cooling .zone-label>svg{color:var(--fan)}.hardware-network .zone-label>svg{color:var(--net-rx)}.hardware-main-value{font-size:27px;line-height:1.15;font-weight:600;font-variant-numeric:tabular-nums;letter-spacing:-.6px;display:flex;align-items:baseline}.power-value{color:var(--power)}.hardware-main-value>small{font-size:14px;font-weight:450;letter-spacing:0;margin-left:4px}.hardware-delta{font-size:12px;font-weight:400;margin-left:auto;letter-spacing:0;opacity:.8}.hardware-psus{display:grid;gap:3px;margin-top:4px}.hardware-psu{line-height:1.15;display:flex;align-items:center;gap:8px;min-width:0;font-variant-numeric:tabular-nums}.hardware-psu>span:first-child{font-size:13px;color:var(--ops-muted-fg);min-width:35px}.psu-load-track{height:5px;flex:1;min-width:16px;max-width:70px;background:color-mix(in srgb,var(--power) 12%,transparent);border-radius:2px;overflow:hidden}.psu-load-track>i{height:100%;display:block;background:var(--power);border-radius:2px;opacity:.8}.hardware-psu strong{margin-left:auto;font-size:16px;font-weight:500;white-space:nowrap;color:var(--power)}.hardware-psu small{font-size:13px;font-weight:400;opacity:.75}.hardware-empty{font-size:14px;color:var(--ops-muted-fg)}
 .thermal-reading{--tone:var(--thermal);display:grid;grid-template-columns:36px auto 1fr;align-items:baseline;gap:4px 7px;margin-top:6px;font-variant-numeric:tabular-nums}.thermal-reading>span:first-child{font-size:13px;color:var(--ops-muted-fg)}.thermal-reading>strong{line-height:1.1;font-size:24px;font-weight:550;color:var(--tone);letter-spacing:-.4px}.thermal-reading>strong>small{font-size:13px;font-weight:400;margin-left:2px}.thermal-reading>.thermal-average{font-size:13px;color:var(--ops-muted-fg);text-align:right;white-space:nowrap}.thermal-track{grid-column:2/-1;height:4px;border-radius:2px;background:color-mix(in srgb,var(--tone) 12%,transparent);overflow:hidden;margin-bottom:3px}.thermal-track>i{display:block;height:100%;background:var(--tone);border-radius:2px}.gpu-thermal{--tone:var(--gpu)}.detail-devices>span[data-device=gpu]{--tone:var(--gpu)}.thermal-reading[data-tone=warning],.detail-devices>span[data-tone=warning]{--tone:var(--thermal-warning)}.thermal-reading[data-tone=critical],.detail-devices>span[data-tone=critical]{--tone:var(--thermal-critical)}.thermal-reading[data-tone=unknown],.detail-devices>span[data-tone=unknown]{--tone:var(--ops-muted-fg)}.hardware-thermal :global(.sparkline){margin-top:5px}
 .cooling-group{display:grid;grid-template-columns:30px 1fr;column-gap:9px;row-gap:5px;align-items:center;margin-top:8px}.cooling-reading{display:flex;align-items:baseline;gap:8px;flex-wrap:wrap}.cooling-reading>span{line-height:1.15;font-size:13px;color:var(--ops-muted-fg)}.cooling-reading>strong{line-height:1.2;font-size:18px;font-weight:550;color:var(--fan);white-space:nowrap;font-variant-numeric:tabular-nums}.cooling-reading small{font-size:13px;font-weight:400;opacity:.8}.cooling-rotors{grid-column:1/-1;display:grid;grid-template-columns:repeat(var(--fan-columns),minmax(0,16px));gap:4px;justify-content:start}.network-reading{display:flex;justify-content:space-between;align-items:baseline;gap:8px;margin-top:9px;font-variant-numeric:tabular-nums}.network-reading>span{font-size:13px}.network-reading>strong{font-size:22px;font-weight:500;letter-spacing:-.4px}.rx-label{color:var(--net-rx)}.tx-label{color:var(--net-tx)}.hardware-network :global(.sparkline){margin-top:9px}
 .hardware-resource-strip{display:flex;align-items:center;gap:22px;border-top:1px solid var(--ops-border);padding:6px 18px;color:var(--ops-muted-fg)}.hardware-resource-strip>div{display:flex;align-items:center;gap:8px;width:190px;max-width:32%;font-size:13px;flex-shrink:0}.resource-track{height:3px;min-width:20px;flex:1;background:color-mix(in srgb,var(--ops-muted-fg) 13%,transparent);border-radius:2px;overflow:hidden}.resource-track>i{display:block;height:100%;background:var(--ops-muted-fg);opacity:.7}.hardware-resource-strip strong{font-size:14px;font-weight:450;color:var(--ops-fg);min-width:35px;text-align:right;font-variant-numeric:tabular-nums}.hardware-issue{font-size:12px;color:var(--warning);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-left:auto}
 .hardware-details{border-top:1px solid var(--ops-border);padding:16px 18px;display:grid;grid-template-columns:1fr 1fr 1.2fr;gap:24px}.hardware-details[hidden]{display:none}.hardware-details table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}.hardware-details th,.hardware-details td{text-align:right;padding:5px;font-size:16px;font-weight:450;color:var(--power)}.hardware-details th{font-size:13px;color:var(--ops-muted-fg)}.hardware-details th:first-child{text-align:left}.hardware-details th:last-child{width:30%}.hardware-details>footer{font-size:12px;color:var(--ops-muted-fg);line-height:1.6}.hardware-details>footer{grid-column:1/-1}.detail-devices{display:grid;grid-template-columns:1fr 1fr;gap:10px 16px}.detail-devices>span{display:flex;justify-content:space-between;gap:10px;--tone:var(--thermal)}.detail-devices>span>span{font-size:13px;color:var(--ops-muted-fg)}.detail-devices strong{font-size:16px;font-weight:500;color:var(--tone)}.detail-fans{display:grid;gap:8px}.detail-fans>span{display:flex;align-items:center;gap:8px}.detail-fans>span>span{font-size:13px;color:var(--ops-muted-fg);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.detail-fans strong{margin-left:auto;font-size:16px;font-weight:500;color:var(--fan)}
 :global(html.light) .hardware-card .psu-load-track{background:color-mix(in srgb,var(--sys-power) 17%,var(--ops-card))}:global(html.light) .hardware-card .psu-load-track>i{opacity:1}

 .psu-load-track.overload>i{background:var(--ops-danger)}
 .psu-unavailable{font-size:12px;color:var(--ops-muted-fg)}
 .psu-distribution{--psu-part-0:var(--power);--psu-part-1:var(--thermal);--psu-part-2:var(--gpu);--psu-part-3:var(--fan);display:grid;gap:5px;margin-top:9px;color:var(--ops-muted-fg)}
 .psu-distribution-track{display:flex;height:6px;border-radius:3px;overflow:hidden}
 .psu-segment{background:var(--psu-color)}
 .psu-distribution-legend{display:flex;flex-wrap:wrap;gap:4px 10px;font-size:12px}
 .psu-distribution-legend>span::before{content:'';display:inline-block;width:7px;height:7px;margin-right:4px;border-radius:2px;background:var(--psu-color)}
 .cooling-reading>span>span{display:block}
 @media(min-width:1100px){.cooling-reading{flex-direction:column;align-items:flex-start;gap:2px}.cooling-group{grid-template-columns:30px 1fr}.cooling-rotors{grid-column:1/-1;max-width:none}}
 @media(max-width:1050px){.hardware-zones{grid-template-columns:1fr 1fr;gap:20px 28px}.hardware-network,.hardware-cooling{border-top:1px solid var(--ops-border);padding-top:12px}.hardware-details{grid-template-columns:1fr 1fr}.hardware-details>section:last-of-type{grid-column:1/-1}.detail-fans{grid-template-columns:1fr 1fr}.hardware-identity{gap:10px}}
 @media(max-width:600px){.hardware-heading{padding:13px 14px}.hardware-identity{flex-wrap:wrap;gap:7px 10px}.hardware-identity>svg{display:none}.hardware-identity>span{font-size:12px}.hardware-identity>span:not(.hardware-status){flex-basis:100%;order:3}.hardware-status{margin:0}.hardware-heading-end>span:not(.hardware-activity){display:none}.hardware-zones{padding:14px;gap:18px 20px}.hardware-card .zone-label{font-size:13px;flex-wrap:wrap;gap:4px}.zone-label>small{font-size:12px;margin-left:0}.hardware-main-value{font-size:25px}.hardware-delta{display:none}.hardware-psu{gap:5px}.hardware-psu>span:first-child{font-size:13px;min-width:31px}.hardware-psu strong{font-size:16px}.hardware-psu small{font-size:12px}.thermal-reading{grid-template-columns:30px 1fr;gap:3px 6px}.thermal-reading>strong{font-size:23px}.thermal-reading>.thermal-average{grid-column:2;font-size:13px;text-align:left}.thermal-track{grid-column:1/-1;margin-top:3px}.cooling-group{grid-template-columns:26px 1fr;column-gap:7px}.cooling-reading{gap:3px}.cooling-reading>strong{font-size:17px}.cooling-reading small{font-size:12px}.cooling-rotors{gap:3px}.network-reading>strong{font-size:20px}.hardware-resource-strip{padding:9px 14px;gap:15px;flex-wrap:wrap}.hardware-resource-strip>div{width:auto;flex:1;max-width:none;min-width:0}.hardware-issue{width:100%;margin-left:0}.hardware-details{grid-template-columns:1fr;padding:15px 14px;gap:22px}.hardware-details>section:last-of-type{grid-column:auto}.detail-fans{grid-template-columns:1fr}.hardware-details td{font-size:16px}}
 @media(max-width:360px){.hardware-zones{grid-template-columns:1fr}.hardware-thermal{border-top:1px solid var(--ops-border);padding-top:12px}.thermal-reading{grid-template-columns:36px auto 1fr}.thermal-reading>.thermal-average{grid-column:auto;text-align:right}.thermal-track{grid-column:2/-1}.hardware-psu small{display:inline}.hardware-psus:after{display:none}}
 @media(prefers-reduced-motion:reduce){.hardware-heading-end>svg{transition:none}}
</style>
