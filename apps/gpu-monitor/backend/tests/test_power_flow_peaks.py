"""The complete metric evaluator remains the oracle for optimized history."""
import copy
import unittest
from datetime import timedelta
from backend.power_flow.schema import FlowGraph
from backend.power_flow.evaluator import evaluate
from backend.power_flow.peaks import CompiledPeaks
from backend.power_flow.service import _merge_peaks
from backend.tests.test_power_flow_engine import T, graph, neumann_graph, node, reading

class CompiledPeakTests(unittest.TestCase):
    def compare(self,g,rounds):
        g=FlowGraph.model_validate(g)
        expected=evaluate(g,[],T);actual=copy.deepcopy(expected)
        compiled=CompiledPeaks(g);incomplete=False
        for at,rows in rounds:
            metric=evaluate(g,rows,at);_merge_peaks(expected,metric,at)
            incomplete=incomplete or any(not item['complete'] for item in metric['nodes'].values())
            compiled.add_round(rows,at)
        compiled.apply(actual)
        self.assertEqual(compiled.incomplete,incomplete)
        for group in ('nodes','edges','ports'):
            for key in expected[group]:
                for field in ('peak_watts','peak_amps','peak_watts_at','peak_amps_at'):
                    self.assertEqual(actual[group][key][field],expected[group][key][field],f'{group}:{key}:{field}')
        return actual

    def test_independent_watt_amp_peaks_ties_and_empty_branches(self):
        g=graph();g['nodes'][0]['ports'].append(dict(id='two',label='',direction='output'))
        g['nodes'].extend([node('p2','psu',parent_id='s',server_id=1,psu_slot='PSU2'),node('unused','breaker',voltage_v=110)])
        g['edges'].append(dict(id='two',source='b',source_port='two',target='p2',target_port='in'))
        result=self.compare(g,[(T,[reading(220),reading(0,'PSU2 Power In')]),(T+timedelta(seconds=30),[reading(0,seconds=30),reading(330,'PSU2 Power In',30)]),(T+timedelta(seconds=60),[reading(0,seconds=60),reading(330,'PSU2 Power In',60)])])
        self.assertNotEqual(result['nodes']['b']['peak_watts_at'],result['nodes']['b']['peak_amps_at'])

    def test_missing_stale_skew_future_invalid_ambiguous_and_output(self):
        g=graph();g['nodes'][0]['ports'].append(dict(id='two',label='',direction='output'))
        g['nodes'].append(node('p2','psu',parent_id='s',server_id=1,psu_slot='PSU2'))
        g['edges'].append(dict(id='two',source='b',source_port='two',target='p2',target_port='in'))
        rows=[[],[reading(None)],[reading(0)],[reading(220,seconds=-76)],[reading(220,seconds=1)],[reading(220),reading(220,'PSU2 Power In',-31)],[reading(-1)],[reading(float('inf'))],[reading(220),reading(230,'PS1 Input Power')],[dict(reading(1000),direction='output')],[reading(220),reading(110,'PSU2 Power In')]]
        self.compare(g,[(T,values) for values in rows])

    def test_distinct_voltages_fixed_zero_and_server_display(self):
        g=graph();g['nodes'].extend([node('b2','breaker',voltage_v=110),node('p2','psu',parent_id='s',server_id=1,psu_slot='PSU2',power_factor=.8),node('zero','fixed',fixed_watts=0)])
        g['edges'].append(dict(id='two',source='b2',source_port='out',target='p2',target_port='in'))
        self.compare(g,[(T,[reading(220),reading(110,'PSU2 Power In')]),(T+timedelta(seconds=30),[reading(110,seconds=30),reading(220,'PSU2 Power In',30)])])

    def test_four_underscore_input_sensors_replay_without_output_fallback(self):
        rows=[reading(value,f'Pwr_PSU{i}_In',server_id=13) for i,value in enumerate((136,144,144,136),1)]
        outputs=[dict(reading(999,f'Pwr_PSU{i}_Out',server_id=13),direction='output') for i in range(1,5)]
        ambiguous=rows+[reading(100,'PSU1 Power In',server_id=13)]
        result=self.compare(neumann_graph(),[(T,outputs),(T,ambiguous),(T,rows+outputs)])
        self.assertEqual(result['nodes']['b']['peak_watts'],560)
        self.assertAlmostEqual(result['nodes']['b']['peak_amps'],560/220)
