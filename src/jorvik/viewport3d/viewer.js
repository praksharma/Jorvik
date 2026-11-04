// Application shell adapted from PyniteGUI; see THIRD_PARTY.md.
import * as THREE from 'three';
import {OrbitControls} from './vendor/OrbitControls.js';
import {OrientationGizmo} from './gizmo.js';
import {LineSegments2} from './vendor/lines/LineSegments2.js';
import {LineSegmentsGeometry} from './vendor/lines/LineSegmentsGeometry.js';
import {LineMaterial} from './vendor/lines/LineMaterial.js';
const scene=new THREE.Scene();
const camera=new THREE.PerspectiveCamera(38,1,.01,10000);
const renderer=new THREE.WebGLRenderer({antialias:true});
renderer.setPixelRatio(Math.min(devicePixelRatio,2));renderer.autoClear=false;
document.body.appendChild(renderer.domElement);
const controls=new OrbitControls(camera,renderer.domElement);
controls.enableDamping=true;controls.screenSpacePanning=true;
const notify=index=>window.geoBridge?.orientation(index);
const gizmo=new OrientationGizmo(camera,controls,renderer,orient,notify,stopMotion);
controls.addEventListener('start',()=>notify(7));
const grid=new THREE.GridHelper(20,20,0xa8b5b8,0xe2e7e8);grid.rotation.x=Math.PI/2;scene.add(grid);
const axes=new THREE.AxesHelper(3);scene.add(axes);
scene.add(new THREE.HemisphereLight(0xffffff,0x708080,2));
const light=new THREE.DirectionalLight(0xffffff,2);light.position.set(1,2,3);scene.add(light);
let objects=[],faces=[],edges=[],documentId='',mode='face',selected=new Set(),hover=null;
let center=new THREE.Vector3(),radius=15,down=null;
const raycaster=new THREE.Raycaster();
const key=o=>`${o.userData.dimension}:${o.userData.tag}`;
let boundaryColours=new Map();
function setBoundaryRegions(regions){
  boundaryColours=new Map();
  for(const region of regions)for(const entity of region.entities){
    const id=`${entity[0]}:${entity[1]}`;
    if(!boundaryColours.has(id))boundaryColours.set(id,region.colour);
  }
  recolor();
}
let palette={face:0x75a8b5,edge:0x304650};
let sampleObjects=[];
let translationPreview=null,previewOffset=new THREE.Vector3();
let previewMatrix=new THREE.Matrix4(),baseCenter=new THREE.Vector3(),baseRadius=15;
let baseGridMatrix=new THREE.Matrix4(),baseAxesMatrix=new THREE.Matrix4();
function objectMatrix(object,matrix){
  object.matrixAutoUpdate=false;object.matrix.copy(matrix);
  object.position.setFromMatrixPosition(matrix);object.matrixWorldNeedsUpdate=true;
}
function rememberGeometryFrame(){
  baseCenter.copy(center);baseRadius=radius;
  grid.matrixAutoUpdate=true;axes.matrixAutoUpdate=true;
  grid.updateMatrix();axes.updateMatrix();
  baseGridMatrix.copy(grid.matrix);baseAxesMatrix.copy(axes.matrix);
}
let geometryVisibility={geometry:true,faces:true,edges:true,isolatedEntities:null,hiddenFaces:[],isolatedFaces:null};
function setGeometryVisibility(config){
  geometryVisibility={...geometryVisibility,...config};
  const region=geometryVisibility.isolatedEntities===null?null:new Set(geometryVisibility.isolatedEntities.map(([d,t])=>`${d}:${t}`));
  for(const object of objects){
    object.visible=geometryVisibility.geometry&&(object.userData.dimension===2?geometryVisibility.faces:geometryVisibility.edges)&&(!region||region.has(key(object)))
      &&(object.userData.dimension!==2||(!geometryVisibility.hiddenFaces.includes(object.userData.tag)
      &&(geometryVisibility.isolatedFaces===null||geometryVisibility.isolatedFaces.includes(object.userData.tag))));
  }
  hover=null;recolor();
}
function applyTranslationPreview(){
  // The backend supplies one authoritative affine transform. Original vertex
  // buffers never change, so repeated previews and scope changes cannot accrue.
  const matrix=new THREE.Matrix4();
  if(translationPreview?.matrix)matrix.fromArray(translationPreview.matrix);
  const whole=translationPreview?.scope==='domain';
  const geometryMatrix=whole?matrix:new THREE.Matrix4();
  const delta=geometryMatrix.clone().multiply(previewMatrix.clone().invert());
  const oldScale=previewMatrix.getMaxScaleOnAxis(),newScale=geometryMatrix.getMaxScaleOnAxis();
  stopMotion();
  camera.position.applyMatrix4(delta);controls.target.applyMatrix4(delta);
  camera.up.transformDirection(delta);
  camera.near*=newScale/oldScale;camera.far*=newScale/oldScale;
  camera.updateProjectionMatrix();
  center.copy(baseCenter).applyMatrix4(geometryMatrix);radius=baseRadius*newScale;
  previewMatrix.copy(geometryMatrix);previewOffset.setFromMatrixPosition(geometryMatrix);
  for(const object of objects)objectMatrix(object,geometryMatrix);
  objectMatrix(grid,geometryMatrix.clone().multiply(baseGridMatrix));
  objectMatrix(axes,geometryMatrix.clone().multiply(baseAxesMatrix));
  for(const object of sampleObjects){
    const affected=whole||translationPreview?.regions.includes(object.userData.label);
    objectMatrix(object,affected?matrix:new THREE.Matrix4());
  }
  scene.updateMatrixWorld(true);controls.update();
}
function setTranslationPreview(setup){translationPreview=setup;applyTranslationPreview();}
function stopMotion(){const damping=controls.enableDamping;controls.enableDamping=false;controls.update();controls.enableDamping=damping;}
function resize(){camera.aspect=Math.max(innerWidth,1)/Math.max(innerHeight,1);camera.updateProjectionMatrix();renderer.setSize(Math.max(innerWidth,1),Math.max(innerHeight,1));}
function fit(direction){
  controls.target.copy(center);
  const distance=radius/Math.sin(THREE.MathUtils.degToRad(camera.fov/2))/Math.min(1,camera.aspect);
  camera.position.copy(center).addScaledVector(direction.clone().normalize(),distance);
  camera.near=Math.max(distance/10000,1e-8);camera.far=distance*100;camera.updateProjectionMatrix();controls.update();
}
function orient(index){
  if(index<0||index>6)return;
  gizmo.cancel();stopMotion();
  camera.up.set(0,index===2||index===5?0:1,index===2?-1:index===5?1:0);
  fit(new THREE.Vector3(...[[1,.7,1],[0,0,1],[0,1,0],[1,0,0],[0,0,-1],[0,-1,0],[-1,0,0]][index]));notify(index);
}
function recolor(){
  for(const object of objects){
    const id=key(object),isSelected=selected.has(id),isHovered=hover===id;
    const color=isSelected?0xe89a34:isHovered?0x41c4b8:(boundaryColours.get(id)??(object.userData.dimension===2?palette.face:palette.edge));
    object.material.color.set(color);
    const highlight=object.userData.highlight;
    if(highlight){
      highlight.visible=isSelected||isHovered;
      highlight.material.color.set(color);
      highlight.material.linewidth=isSelected?6:4;
    }
  }
}
function appearance(config){
  scene.background=new THREE.Color(config.canvas);grid.material.color.set(config.grid);
  grid.visible=config.showGrid;axes.visible=config.showAxes;
  const dark=new THREE.Color(config.canvas).getHSL({}).l<.4;
  palette={face:dark?0x6196ad:0x75a8b5,edge:dark?0xd0e4ec:0x304650};recolor();
}
function loadGeometry(data){
  translationPreview=null;previewOffset.set(0,0,0);previewMatrix.identity();
  for(const object of objects){scene.remove(object);object.traverse(part=>{part.geometry?.dispose();part.material?.dispose();});}
  objects=[];faces=[];edges=[];selected.clear();hover=null;down=null;documentId=data?.document??null;
  if(!data){center.set(0,0,0);radius=15;grid.position.set(0,0,0);grid.scale.setScalar(1);axes.position.set(0,0,0);axes.scale.setScalar(1);rememberGeometryFrame();orient(0);return;}
  const box=new THREE.Box3();
  for(const entity of data.entities){
    if(!entity.positions.length)continue;
    const geometry=new THREE.BufferGeometry();geometry.setAttribute('position',new THREE.Float32BufferAttribute(entity.positions,3));
    let object;
    if(entity.dimension===2){
      geometry.computeVertexNormals();
      object=new THREE.Mesh(geometry,new THREE.MeshStandardMaterial({color:palette.face,side:THREE.DoubleSide,roughness:.65,polygonOffset:true,polygonOffsetFactor:1,polygonOffsetUnits:1}));faces.push(object);
    }else{object=new THREE.LineSegments(geometry,new THREE.LineBasicMaterial({color:palette.edge}));edges.push(object);}
    object.userData={dimension:entity.dimension,tag:entity.tag};
    if(entity.dimension===1){
      // Screen-space ribbons: native WebGL lines remain one pixel on most GPUs.
      const highlight=new LineSegments2(new LineSegmentsGeometry().setPositions(entity.positions),
        new LineMaterial({color:0xe89a34,linewidth:6,worldUnits:false,depthTest:true,depthWrite:false}));
      highlight.visible=false;
      object.add(highlight);object.userData.highlight=highlight;
    }
    objects.push(object);scene.add(object);box.expandByObject(object);
  }
  box.getCenter(center);radius=Math.max(box.getSize(new THREE.Vector3()).length()*.6,1e-6);
  grid.position.copy(center);grid.position.z=box.min.z-radius*.005;grid.scale.setScalar(radius/8);
  axes.position.copy(box.min);axes.scale.setScalar(radius/8);
  rememberGeometryFrame();orient(data.dimension===2?1:0);recolor();
}
function setMode(value){
  mode=value==='edge'?'edge':'face';hover=null;down=null;recolor();
  // Distinct cursors accompany the explicit toolbar modes.
  renderer.domElement.style.cursor=mode==='face'?'crosshair':'cell';
}
function setSelection(values){selected=new Set(values.map(([d,t])=>`${d}:${t}`));recolor();}
function pickAt(x,y){
  const rect=renderer.domElement.getBoundingClientRect();
  scene.updateMatrixWorld(true);camera.updateMatrixWorld(true);
  const visibleFaces=faces.filter(o=>o.visible);
  const frontSurface=(sx,sy)=>{
    raycaster.setFromCamera(new THREE.Vector2((sx-rect.left)/rect.width*2-1,1-(sy-rect.top)/rect.height*2),camera);
    return raycaster.intersectObjects(visibleFaces,false).find(hit=>{
      const z=hit.point.clone().project(camera).z;return z>=-1&&z<=1;
    });
  };
  if(mode==='face')return frontSurface(x,y)?.object||null;
  // Compare projected segments in pixels, independent of target depth or zoom.
  // A nearby edge is eligible only if its closest screen point is itself visible.
  const candidates=[];
  for(const object of edges){
    if(!object.visible)continue;
    const positions=object.geometry.attributes.position;
    for(let i=0;i+1<positions.count;i+=2){
      let a=new THREE.Vector3().fromBufferAttribute(positions,i).applyMatrix4(object.matrixWorld).applyMatrix4(camera.matrixWorldInverse);
      let b=new THREE.Vector3().fromBufferAttribute(positions,i+1).applyMatrix4(object.matrixWorld).applyMatrix4(camera.matrixWorldInverse);
      const da=-a.z,db=-b.z,delta=db-da;
      // Clip segments to the camera's depth slab before perspective projection.
      if(Math.abs(delta)<1e-15){if(da<camera.near||da>camera.far)continue;}
      else{
        const t0=(camera.near-da)/delta,t1=(camera.far-da)/delta;
        const low=Math.max(0,Math.min(t0,t1)),high=Math.min(1,Math.max(t0,t1));
        if(low>high)continue;
        const start=a.clone(),end=b.clone();a.lerpVectors(start,end,low);b.lerpVectors(start,end,high);
      }
      const pa=a.clone().applyMatrix4(camera.projectionMatrix),pb=b.clone().applyMatrix4(camera.projectionMatrix);
      const ax=rect.left+(pa.x+1)*rect.width/2,ay=rect.top+(1-pa.y)*rect.height/2;
      const bx=rect.left+(pb.x+1)*rect.width/2,by=rect.top+(1-pb.y)*rect.height/2;
      const dx=bx-ax,dy=by-ay,length2=dx*dx+dy*dy;
      const t=length2?THREE.MathUtils.clamp(((x-ax)*dx+(y-ay)*dy)/length2,0,1):0;
      const sx=ax+t*dx,sy=ay+t*dy,pixels=Math.hypot(x-sx,y-sy);
      if(pixels>6)continue;
      // Perspective-correct interpolation recovers the 3D point under (sx,sy).
      const u=(t/(-b.z))/((1-t)/(-a.z)+t/(-b.z));
      const point=a.clone().lerp(b,u).applyMatrix4(camera.matrixWorld);
      candidates.push({object,sx,sy,pixels,distance:point.distanceTo(camera.position)});
    }
  }
  candidates.sort((a,b)=>a.pixels-b.pixels||a.distance-b.distance);
  for(const hit of candidates){
    const surface=frontSurface(hit.sx,hit.sy);
    const epsilon=Math.max(hit.distance,camera.near)*1e-6;
    if(!surface||hit.distance<=surface.distance+epsilon)return hit.object;
  }
  return null;
}
renderer.domElement.addEventListener('pointerdown',event=>{if(event.button===0)down={x:event.clientX,y:event.clientY,moved:false};});
renderer.domElement.addEventListener('pointermove',event=>{
  if(down&&Math.hypot(event.clientX-down.x,event.clientY-down.y)>4)down.moved=true;
  const object=event.buttons?null:pickAt(event.clientX,event.clientY);
  hover=object?key(object):null;recolor();
});
renderer.domElement.addEventListener('pointerleave',()=>{hover=null;recolor();});
renderer.domElement.addEventListener('pointercancel',()=>{down=null;});
renderer.domElement.addEventListener('pointerup',event=>{
  const start=down;down=null;
  if(event.button!==0||!start||start.moved||Math.hypot(event.clientX-start.x,event.clientY-start.y)>4)return;
  const object=pickAt(event.clientX,event.clientY);
  window.geoBridge?.picked(documentId,object?.userData.dimension||0,object?.userData.tag||0,event.ctrlKey||event.metaKey||event.shiftKey);
});
// One shared sprite keeps large clouds cheap: a light circle with a dark rim.
// The outline preserves contrast on selected orange faces and light canvases.
const pointCanvas=document.createElement('canvas');pointCanvas.width=pointCanvas.height=64;
const pointContext=pointCanvas.getContext('2d');
pointContext.fillStyle='#151515';pointContext.beginPath();pointContext.arc(32,32,30,0,Math.PI*2);pointContext.fill();
pointContext.fillStyle='#ffffff';pointContext.beginPath();pointContext.arc(32,32,23,0,Math.PI*2);pointContext.fill();
const pointTexture=new THREE.CanvasTexture(pointCanvas);pointTexture.colorSpace=THREE.SRGBColorSpace;
let sampleStyle={size:7,visible:true,through:true,hiddenLabels:[],isolatedLabel:null,colours:{}};
function setSampleStyle(config){
  sampleStyle={...sampleStyle,...config};
  for(const object of sampleObjects){
    object.material.color.set(sampleStyle.colours[object.userData.label]??object.userData.defaultColour);
    object.material.size=sampleStyle.size;
    object.material.depthTest=!sampleStyle.through;
    object.visible=sampleStyle.visible&&!sampleStyle.hiddenLabels.includes(object.userData.label)&&(!sampleStyle.isolatedLabel||sampleStyle.isolatedLabel===object.userData.label);
  }
}
function setSamples(config){
  for(const object of sampleObjects){scene.remove(object);object.geometry.dispose();object.material.dispose();}
  sampleObjects=[];
  const colors=[0xfff6dc,0x83e5ed,0xb5ef86,0xaabfff,0xffffff];
  for(const [index,batch] of config.batches.entries()){
    const geometry=new THREE.BufferGeometry();
    geometry.setAttribute('position',new THREE.Float32BufferAttribute(batch.coordinates.flat(),3));
    const object=new THREE.Points(geometry,new THREE.PointsMaterial({color:colors[index%colors.length],map:pointTexture,alphaTest:0.15,sizeAttenuation:false,depthWrite:false}));
    object.userData.label=batch.label;object.userData.defaultColour=colors[index%colors.length];object.renderOrder=2;
    scene.add(object);sampleObjects.push(object);
  }
  setSampleStyle({size:config.size??7,visible:config.visible,through:config.through,hiddenLabels:config.hiddenLabels??[],isolatedLabel:config.isolatedLabel??null,colours:config.colours??{}});
  applyTranslationPreview();
}
function cameraState(){return {position:camera.position.toArray(),target:controls.target.toArray(),up:camera.up.toArray(),near:camera.near,far:camera.far};}
function setCameraState(state){
  stopMotion();camera.position.fromArray(state.position);camera.up.fromArray(state.up);
  controls.target.fromArray(state.target);camera.near=state.near;camera.far=state.far;
  camera.updateProjectionMatrix();controls.update();notify(7);
}
function transformedPoint(object){
  const positions=object.geometry.attributes.position;
  return positions?.count?new THREE.Vector3().fromBufferAttribute(positions,0).applyMatrix4(object.matrix).toArray():null;
}
function diagnostics(){
  const gl=renderer.getContext(),extension=gl.getExtension('WEBGL_debug_renderer_info');
  return {renderer:gl.getParameter(extension?extension.UNMASKED_RENDERER_WEBGL:gl.RENDERER),
    vendor:gl.getParameter(extension?extension.UNMASKED_VENDOR_WEBGL:gl.VENDOR),
    entityColours:Object.fromEntries(objects.map(o=>[key(o),`#${o.material.color.getHexString()}`])),version:gl.getParameter(gl.VERSION),lost:gl.isContextLost(),visibleFaces:faces.filter(o=>o.visible).length,visibleEdges:edges.filter(o=>o.visible).length,sampleColours:Object.fromEntries(sampleObjects.map(o=>[o.userData.label,`#${o.material.color.getHexString()}`])),preview:translationPreview,sampleOffsets:Object.fromEntries(sampleObjects.map(o=>[o.userData.label,o.position.toArray()])),geometryOffset:previewOffset.toArray(),geometryMatrix:previewMatrix.toArray(),
    sampleMatrices:Object.fromEntries(sampleObjects.map(o=>[o.userData.label,o.matrix.toArray()])),
    firstSamplePoints:Object.fromEntries(sampleObjects.map(o=>[o.userData.label,transformedPoint(o)])),
    firstGeometryPoints:Object.fromEntries(objects.map(o=>[key(o),transformedPoint(o)])),
    selectionCount:selected.size,pointSize:sampleStyle.size,sampleVisible:sampleStyle.visible,visibleSampleCount:sampleObjects.reduce((n,o)=>n+(o.visible?o.geometry.attributes.position.count:0),0),sampleCount:sampleObjects.reduce((n,o)=>n+o.geometry.attributes.position.count,0)};
}
window.geoViewer={setBoundaryRegions,cameraState,setCameraState,setTranslationPreview,orient,appearance,loadGeometry,setMode,setSelection,setGeometryVisibility,setSamples,setSampleStyle,diagnostics,reset:()=>orient(0)};
renderer.domElement.addEventListener('webglcontextlost',event=>{event.preventDefault();window.geoReportFailure('Graphics context lost. Restart the application.');});
addEventListener('resize',resize);resize();appearance({canvas:'#fafcfc',grid:'#e2e7e8',showGrid:false,showAxes:true});orient(0);setMode('face');
const clock=new THREE.Clock();renderer.setAnimationLoop(()=>{gizmo.update(clock.getDelta());controls.update();renderer.clear();renderer.render(scene,camera);gizmo.render();});
window.geoRendererReady=true;window.geoBridge?.ready();
