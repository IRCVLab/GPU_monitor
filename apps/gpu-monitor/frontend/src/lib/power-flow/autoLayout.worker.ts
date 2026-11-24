import type { FlowGraph } from './types';
// @ts-expect-error TS5097: use the same module in the browser worker and Node's native TypeScript runner.
import { compactLayout } from './compactLayout.ts';
self.onmessage=({data}:{data:FlowGraph})=>{try{self.postMessage({result:compactLayout(data)});}catch(error){self.postMessage({error:error instanceof Error?error.message:'Automatic layout failed.'});}};
