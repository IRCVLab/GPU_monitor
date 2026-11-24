type Bounds={x:number;y:number;width:number;height:number};
type Viewport={x:number;y:number;zoom:number};
/** Move only enough to reveal a selected path; never shrink the operator's current zoom. */
export function revealViewport(viewport:Viewport,path:Bounds,selected:Bounds,width:number,height:number):Viewport{
 const margin=24,zoom=viewport.zoom;
 const axis=(offset:number,start:number,size:number,fallbackStart:number,fallbackSize:number,available:number)=>{
  if(size*zoom>available-margin*2){start=fallbackStart;size=fallbackSize;}
  const min=offset+start*zoom,max=min+size*zoom;
  if(min<margin)return offset+margin-min;
  if(max>available-margin)return offset+available-margin-max;
  return offset;
 };
 return {x:axis(viewport.x,path.x,path.width,selected.x,selected.width,width),y:axis(viewport.y,path.y,path.height,selected.y,selected.height,height),zoom};
}
