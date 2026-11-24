import copy
import unittest
from datetime import datetime, timedelta, timezone
from pydantic import ValidationError
from backend.power_flow.schema import FlowGraph
from backend.power_flow.evaluator import evaluate, normalize_slot

T = datetime(2026, 11, 13, 0, tzinfo=timezone.utc)

def node(id, kind, **values):
    return dict(id=id,kind=kind,label=id,x=0,y=0,ports=([dict(id='in',label='in',direction='input',capacity_a=None)] if kind not in ('breaker','server') else []) + ([dict(id='out',label='out',direction='output',capacity_a=None)] if kind in ('breaker','outlet','strip') else []),**values)

def graph():
    return dict(nodes=[node('b','breaker',capacity_a=16,voltage_v=220),node('s','server',server_id=1),node('p','psu',parent_id='s',server_id=1,psu_slot='PSU1',power_factor=.5),node('f','fixed',fixed_watts=220)],edges=[dict(id='e',source='b',source_port='out',target='p',target_port='in',capacity_a=1)],settings=dict(phase='single',voltage_v=220,power_factor=1,max_age_seconds=75,max_skew_seconds=30))

def reading(watts=220,slot='PSU1 Power In',seconds=0,server_id=1):
    return dict(server_id=server_id,sensor_name=slot,direction='input',power_watts=watts,observed_at=T+timedelta(seconds=seconds),status='ok')

def neumann_graph():
    strip=node('strip','strip',capacity_a=16)
    strip['ports']=[dict(id='in',label='in',direction='input',capacity_a=None)]+[dict(id=f'out{i}',label=f'out{i}',direction='output',capacity_a=None) for i in range(1,5)]
    return dict(nodes=[node('b','breaker',capacity_a=30,voltage_v=220),strip,node('s','server',server_id=13)]+[node(f'p{i}','psu',parent_id='s',server_id=13,psu_slot=f'PSU{i}') for i in range(1,5)],edges=[dict(id='feed',source='b',source_port='out',target='strip',target_port='in')]+[dict(id=f'e{i}',source='strip',source_port=f'out{i}',target=f'p{i}',target_port='in') for i in range(1,5)])

class EvaluationTests(unittest.TestCase):
    def test_pf_voltage_and_independent_limits(self):
        value=evaluate(FlowGraph.model_validate(graph()),[reading()],T)
        self.assertEqual(value['nodes']['b']['watts'],220)
        self.assertEqual(value['nodes']['b']['amps'],2)
        self.assertEqual(value['nodes']['b']['status'],'estimated')
        self.assertEqual(value['edges']['e']['status'],'overload')
        self.assertEqual(value['ports']['b:out']['status'],'unknown')
        self.assertEqual(value['nodes']['s']['watts'],220)
        self.assertTrue(value['nodes']['b']['amps_estimated'])
        self.assertEqual(value['nodes']['b']['capacity_watts'],3520)
    def test_missing_null_stale_and_zero(self):
        for readings in ([],[reading(None)],[reading(220,seconds=-76)],[reading(220,seconds=1)]):
            value=evaluate(FlowGraph.model_validate(graph()),readings,T)['nodes']['b']
            self.assertIsNone(value['watts']);self.assertIsNone(value['amps'])
            self.assertFalse(value['complete']);self.assertEqual(value['status'],'unknown')
        value=evaluate(FlowGraph.model_validate(graph()),[reading(0)],T)['nodes']['b']
        self.assertEqual(value['watts'],0);self.assertEqual(value['amps'],0);self.assertTrue(value['complete'])
    def test_missing_source_preserves_known_lower_bound(self):
        g=graph();g['nodes'][0]['ports'].append(dict(id='other',label='other',direction='output',capacity_a=1))
        g['nodes'].append(node('p2','psu',parent_id='s',server_id=1,psu_slot='PSU2'))
        g['edges'].append(dict(id='e2',source='b',source_port='other',target='p2',target_port='in'))
        value=evaluate(FlowGraph.model_validate(g),[reading()],T)['nodes']['b']
        self.assertIsNone(value['watts']);self.assertEqual(value['known_watts'],220)
        g['nodes'][0]['capacity_a']=.5
        self.assertEqual(evaluate(FlowGraph.model_validate(g),[reading()],T)['nodes']['b']['status'],'overload')
    def test_unconnected_psu_has_watts_but_not_root_load(self):
        g=graph();g['edges']=[]
        result=evaluate(FlowGraph.model_validate(g),[reading()],T)
        self.assertEqual(result['nodes']['p']['watts'],220)
        self.assertEqual(result['nodes']['p']['status'],'unconnected')
        self.assertEqual(result['nodes']['b']['watts'],0)
    def test_slots_only_normalize_explicit_psu_inputs(self):
        self.assertEqual(normalize_slot('PS1 Input Power'),'PSU1')
        self.assertEqual(normalize_slot('PSU #02 Power In'),'PSU2')
        self.assertIsNone(normalize_slot('System Power'))

    def test_underscore_psu_names_preserve_direction_and_ambiguity_rules(self):
        for slot in range(1,5):
            for direction in ('In','Out'):
                self.assertEqual(normalize_slot(f'Pwr_PSU{slot}_{direction}'),f'PSU{slot}')
        for name in ('Pwr_APSU1_In','Pwr_PSU1x_In','Pwr_PSU0_In'):
            self.assertIsNone(normalize_slot(name))
        g=FlowGraph.model_validate(neumann_graph())
        rows=[reading(value,f'Pwr_PSU{i}_In',server_id=13) for i,value in enumerate((136,144,144,136),1)]
        outputs=[dict(reading(999,f'Pwr_PSU{i}_Out',server_id=13),direction='output') for i in range(1,5)]
        result=evaluate(g,rows+outputs,T)
        for key in ('b','strip','s'):
            self.assertEqual(result['nodes'][key]['watts'],560)
            self.assertAlmostEqual(result['nodes'][key]['amps'],560/220)
        self.assertIsNone(evaluate(g,outputs,T)['nodes']['b']['watts'])
        duplicate=reading(200,'PSU1 Power In',server_id=13)
        self.assertIsNone(evaluate(g,rows+[duplicate],T)['nodes']['p1']['watts'])

class ValidationTests(unittest.TestCase):
    def invalid(self,g):
        with self.assertRaises(ValidationError):FlowGraph.model_validate(g)
    def test_duplicate_socket_wrong_direction_cycle_and_binding(self):
        for mutate in (
            lambda g:g['edges'].append(dict(id='other',source='b',source_port='out',target='f',target_port='in')),
            lambda g:g['edges'][0].update(source_port='missing'),
            lambda g:g['nodes'].append(copy.deepcopy(g['nodes'][2])),
            lambda g:g['nodes'][2].update(parent_id='b'),
            lambda g:g['nodes'][0].update(capacity_a=0),
            lambda g:g['nodes'][2].update(power_factor=0),
            lambda g:g['nodes'][0].update(voltage_v=float('inf')),
        ):
            g=graph();mutate(g);self.invalid(g)
    def test_duplicate_real_binding_different_node_id(self):
        g=graph();p=copy.deepcopy(g['nodes'][2]);p['id']='second';g['nodes'].append(p);self.invalid(g)
    def test_rack_is_optional_server_metadata_with_a_bounded_label(self):
        old=FlowGraph.model_validate(graph())
        self.assertIsNone(old.nodes[1].rack)
        grouped=graph();grouped['nodes'][1]['rack']='1'
        parsed=FlowGraph.model_validate(grouped)
        self.assertEqual(parsed.nodes[1].rack,'1')
        self.assertEqual(evaluate(parsed,[reading()],T),evaluate(old,[reading()],T))
        for value in ('','x'*65):
            invalid=graph();invalid['nodes'][1]['rack']=value;self.invalid(invalid)
        invalid=graph();invalid['nodes'][2]['rack']='1';self.invalid(invalid)

    def test_parallel_different_psus_are_valid(self):
        g=graph();g['nodes'].append(node('p2','psu',parent_id='s',server_id=1,psu_slot='PSU2'));FlowGraph.model_validate(g)

class FurtherSafetyTests(unittest.TestCase):
    def test_cycle_group_handles_and_resource_limits(self):
        g=graph();g['nodes']=[node('a','strip'),node('b','strip')];g['edges']=[dict(id='ab',source='a',source_port='out',target='b',target_port='in'),dict(id='ba',source='b',source_port='out',target='a',target_port='in')]
        with self.assertRaises(ValidationError):FlowGraph.model_validate(g)
        for kind in ('nodes','edges'):
            g=graph();g[kind]=g[kind]*501
            with self.assertRaises(ValidationError):FlowGraph.model_validate(g)
        g=graph();g['nodes'][1]['ports']=[dict(id='out',label='',direction='output')]
        with self.assertRaises(ValidationError):FlowGraph.model_validate(g)
    def test_warning_only_when_explicit_and_port_limits_independent(self):
        g=graph();g['nodes'][0]['capacity_a']=2.1
        g['nodes'][0]['ports'][0]['capacity_a']=1
        result=evaluate(FlowGraph.model_validate(g),[reading()],T)
        self.assertEqual(result['nodes']['b']['status'],'estimated');self.assertEqual(result['ports']['b:out']['status'],'overload')
        g['nodes'][0]['warning_a']=1.9
        self.assertEqual(evaluate(FlowGraph.model_validate(g),[reading()],T)['nodes']['b']['status'],'warning')
    def test_two_breakers_inherit_distinct_voltage_without_server_double_count(self):
        g=graph();g['nodes'].extend([node('b2','breaker',voltage_v=110,capacity_a=16),node('p2','psu',parent_id='s',server_id=1,psu_slot='PSU2')]);g['edges'].append(dict(id='e2',source='b2',source_port='out',target='p2',target_port='in'))
        result=evaluate(FlowGraph.model_validate(g),[reading(),reading(110,'PSU2 Power In')],T)
        self.assertEqual(result['nodes']['b']['watts'],220);self.assertEqual(result['nodes']['b2']['watts'],110)
        self.assertEqual(result['nodes']['b2']['amps'],1);self.assertEqual(result['nodes']['s']['watts'],330)
    def test_output_and_unrecognized_input_never_supply_actual_psu(self):
        rows=[dict(reading(),direction='output'),reading(999,'System Power')]
        self.assertIsNone(evaluate(FlowGraph.model_validate(graph()),rows,T)['nodes']['p']['watts'])

    def test_skewed_samples_cannot_form_a_lower_bound_or_false_overload(self):
        g=graph();g['nodes'][0]['capacity_a']=1.5;g['nodes'][2]['power_factor']=1
        g['nodes'][0]['ports'].append(dict(id='two',label='two',direction='output'))
        g['nodes'].append(node('p2','psu',parent_id='s',server_id=1,psu_slot='PSU2'))
        g['edges'].append(dict(id='e2',source='b',source_port='two',target='p2',target_port='in'))
        value=evaluate(FlowGraph.model_validate(g),[reading(seconds=-31),reading(220,'PSU2 Power In')],T)
        root=value['nodes']['b']
        self.assertIsNone(root['watts']);self.assertIsNone(root['amps'])
        self.assertEqual(root['known_watts'],0);self.assertEqual(root['known_amps'],0)
        self.assertEqual(root['status'],'unknown');self.assertFalse(root['complete'])
        self.assertIn('time_skew',root['missing_psus'])
        self.assertEqual(value['nodes']['p']['watts'],220)
        self.assertEqual(value['nodes']['p2']['watts'],220)
