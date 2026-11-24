export type FlowNodeKind='breaker'|'outlet'|'strip'|'server'|'psu'|'fixed';
export interface FlowPort {id:string;label:string;direction:'input'|'output';capacity_a:number|null}
export interface FlowNode {
 id:string;kind:FlowNodeKind;label:string;x:number;y:number;parent_id:string|null;rack?:string|null;
 capacity_a:number|null;warning_a:number|null;voltage_v:number|null;power_factor:number|null;
 fixed_watts:number|null;server_id:number|null;psu_slot:string|null;ports:FlowPort[];
}
export interface FlowEdge {id:string;label:string;source:string;source_port:string;target:string;target_port:string;capacity_a:number|null;warning_a:number|null}
export interface FlowSettings {phase:'single';voltage_v:number;power_factor:number;max_age_seconds:number;max_skew_seconds:number}
export interface FlowGraph {nodes:FlowNode[];edges:FlowEdge[];settings:FlowSettings}
export interface DiagramSummary {id:string;name:string;version:number;is_scenario:boolean;created_at:string;updated_at:string}
export interface FlowDiagram extends DiagramSummary {graph:FlowGraph;effective_from:string}
export type FlowStatus='ok'|'estimated'|'warning'|'overload'|'unknown'|'unconnected';
export interface FlowMetric {
 watts:number|null;amps:number|null;known_watts:number;known_amps:number;complete:boolean;amps_estimated:boolean;
 capacity_a:number|null;capacity_watts:number|null;utilization:number|null;status:FlowStatus;
 voltage_v:number;power_factor:number;observed_at:string|null;missing_psus:string[];
 peak_watts:number|null;peak_amps:number|null;peak_watts_at:string|null;peak_amps_at:string|null;
}
export type FlowHistoryStatus='pending'|'available'|'partial'|'projection'|'no_data'|'out_of_retention'|'before_topology';
export interface FlowState {peaks_included?:boolean;diagram_id:string|null;version:number|null;at:string;graph:FlowGraph|null;effective_from:string|null;nodes:Record<string,FlowMetric>;edges:Record<string,FlowMetric>;ports:Record<string,FlowMetric>;unmapped_psus:CatalogPsuRef[];history_status:FlowHistoryStatus;is_scenario:boolean}
export interface CatalogPsuRef {server_id:number;server_name:string;slot:string;sensor_name:string;watts:number|null;observed_at:string|null;status:string|null}
export interface CatalogServer {server_id:number;name:string;psus:CatalogPsuRef[]}
export interface FlowCatalog {servers:CatalogServer[];interval_seconds:number}
export type FlowSelection={kind:'node'|'edge';id:string};
export type FlowChangeReason='layout'|'connect'|'delete';
