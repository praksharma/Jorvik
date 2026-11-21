// Application shell adapted from PyniteGUI; see THIRD_PARTY.md.
import * as THREE from 'three';
import {OrbitControls} from './vendor/OrbitControls.js';
import {OrientationGizmo} from './gizmo.js';
import {LineSegments2} from './vendor/lines/LineSegments2.js';
import {LineSegmentsGeometry} from './vendor/lines/LineSegmentsGeometry.js';
import {LineMaterial} from './vendor/lines/LineMaterial.js';
const scene=new THREE.Scene();
const camera=new THREE.PerspectiveCamera(38,1,.01,10000);
let projectionMode='perspective';
const renderer=new THREE.WebGLRenderer({antialias:true,preserveDrawingBuffer:true});
renderer.setPixelRatio(Math.min(devicePixelRatio,2));renderer.autoClear=false;
renderer.localClippingEnabled=true;
document.body.appendChild(renderer.domElement);
const controls=new OrbitControls(camera,renderer.domElement);
controls.enableDamping=true;controls.screenSpacePanning=true;
camera.updateProjectionMatrix=function(){
  if(projectionMode==='orthographic'){
    const dist=Math.max(this.position.distanceTo(controls.target),1e-4);
    const halfH=dist*Math.tan(THREE.MathUtils.degToRad(this.fov*0.5))/Math.max(this.zoom||1,1e-4);
    const halfW=halfH*Math.max(this.aspect||1,1e-4);
    this.left=-halfW;this.right=halfW;this.top=halfH;this.bottom=-halfH;
    this.projectionMatrix.makeOrthographic(this.left,this.right,this.top,this.bottom,this.near,this.far);
    this.projectionMatrixInverse.copy(this.projectionMatrix).invert();
  }else{
    THREE.PerspectiveCamera.prototype.updateProjectionMatrix.call(this);
  }
};
controls.addEventListener('change',()=>{if(projectionMode==='orthographic')camera.updateProjectionMatrix();});
const notify=index=>window.geoBridge?.orientation(index);
const gizmo=new OrientationGizmo(camera,controls,renderer,orient,notify,stopMotion);
controls.addEventListener('start',()=>notify(7));
const grid=new THREE.GridHelper(20,20,0xa8b5b8,0xe2e7e8);grid.rotation.x=Math.PI/2;scene.add(grid);
const axes=new THREE.AxesHelper(3);scene.add(axes);
scene.add(new THREE.HemisphereLight(0xffffff,0x708080,2));
const light=new THREE.DirectionalLight(0xffffff,2);light.position.set(1,2,3);scene.add(light);
let objects=[],faces=[],edges=[],documentId='',mode='face',selected=new Set(),hover=null;
let center=new THREE.Vector3(),radius=15,down=null;
let geomBoxMin=new THREE.Vector3(-1,-1,-1),geomBoxMax=new THREE.Vector3(1,1,1);
let showBoundingBox=false,bboxHelper=null,coordUnits='';
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
let sampleObjects=[],glyphObjects=[];
let shadingMode='opaque';
let clipConfig={axis:'off',position:0.0,flip:false,clipSamples:false};
let activeClipPlane=null;
let scalarRange=null;
const COLORMAPS={
  viridis:[
    [0.267,0.005,0.329],[0.283,0.141,0.458],[0.254,0.265,0.530],[0.207,0.372,0.553],
    [0.164,0.471,0.558],[0.128,0.567,0.551],[0.135,0.659,0.518],[0.267,0.749,0.441],
    [0.478,0.821,0.318],[0.741,0.873,0.150],[0.993,0.906,0.144]
  ],
  plasma:[
    [0.050,0.030,0.528],[0.254,0.014,0.615],[0.417,0.001,0.658],[0.563,0.051,0.642],
    [0.693,0.165,0.565],[0.798,0.280,0.470],[0.881,0.393,0.383],[0.949,0.518,0.296],
    [0.988,0.652,0.211],[0.988,0.805,0.145],[0.940,0.975,0.131]
  ],
  coolwarm:[
    [0.230,0.299,0.754],[0.383,0.509,0.917],[0.554,0.690,0.996],[0.722,0.814,0.976],
    [0.865,0.865,0.865],[0.960,0.768,0.678],[0.957,0.603,0.483],[0.867,0.374,0.296],[0.706,0.016,0.150]
  ]
};
const COLORMAP_CSS={
  viridis:'linear-gradient(90deg, #440154 0%, #3b528b 25%, #21918c 50%, #5ec962 75%, #fde725 100%)',
  plasma:'linear-gradient(90deg, #0d0887 0%, #7e03a8 25%, #cc4778 50%, #f89540 75%, #f0f921 100%)',
  coolwarm:'linear-gradient(90deg, #3b4cc0 0%, #8db0fe 25%, #dddddd 50%, #f49a7b 75%, #b40426 100%)'
};
function evalColormap(name,t){
  const stops=COLORMAPS[name]||COLORMAPS.viridis;
  const u=Math.max(0,Math.min(1,Number.isFinite(t)?t:0.5))*(stops.length-1);
  const idx=Math.min(stops.length-2,Math.floor(u)),frac=u-idx;
  const a=stops[idx],b=stops[idx+1];
  return [a[0]+(b[0]-a[0])*frac,a[1]+(b[1]-a[1])*frac,a[2]+(b[2]-a[2])*frac];
}
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
function setProjectionMode(modeName){
  projectionMode=modeName==='orthographic'?'orthographic':'perspective';
  camera.updateProjectionMatrix();
}
function updateBboxHud(){
  const hud=document.getElementById('bbox-hud');
  if(bboxHelper)bboxHelper.visible=showBoundingBox&&objects.length>0;
  if(!hud)return;
  if(!showBoundingBox||!objects.length){hud.style.display='none';return;}
  const dx=geomBoxMax.x-geomBoxMin.x,dy=geomBoxMax.y-geomBoxMin.y,dz=geomBoxMax.z-geomBoxMin.z;
  const fmt=v=>Number(v.toPrecision(4)).toString();
  const unitSuffix=coordUnits?` ${coordUnits}`:'';
  hud.textContent=`Bounds: ${fmt(dx)} × ${fmt(dy)} × ${fmt(dz)}${unitSuffix}  |  X:[${fmt(geomBoxMin.x)}, ${fmt(geomBoxMax.x)}] Y:[${fmt(geomBoxMin.y)}, ${fmt(geomBoxMax.y)}] Z:[${fmt(geomBoxMin.z)}, ${fmt(geomBoxMax.z)}]`;
  hud.style.display='block';
}
function setBoundingBoxVisible(visible){
  showBoundingBox=Boolean(visible);
  updateBboxHud();
}
function setShadingMode(modeName){
  shadingMode=['opaque','translucent','wireframe'].includes(modeName)?modeName:'opaque';
  for(const face of faces){
    face.material.wireframe=shadingMode==='wireframe';
    face.material.transparent=shadingMode==='translucent';
    face.material.opacity=shadingMode==='translucent'?0.32:1.0;
    face.material.depthWrite=shadingMode!=='translucent';
    face.material.needsUpdate=true;
  }
}
function applyClippingPlanes(){
  const axis=clipConfig.axis;
  if(!['x','y','z'].includes(axis)){
    activeClipPlane=null;
  }else{
    const idx=axis==='x'?0:(axis==='y'?1:2);
    const low=geomBoxMin.getComponent(idx),high=geomBoxMax.getComponent(idx);
    const span=Math.max(high-low,1e-6);
    const coord=0.5*(low+high)+0.5*span*Math.max(-1,Math.min(1,Number(clipConfig.position)||0));
    const sign=clipConfig.flip?1:-1;
    const normal=new THREE.Vector3(axis==='x'?sign:0,axis==='y'?sign:0,axis==='z'?sign:0);
    activeClipPlane=new THREE.Plane(normal,-sign*coord);
  }
  const cadPlanes=activeClipPlane?[activeClipPlane]:[];
  const samplePlanes=(activeClipPlane&&clipConfig.clipSamples)?[activeClipPlane]:[];
  for(const object of objects){
    object.material.clippingPlanes=cadPlanes;
    object.material.needsUpdate=true;
    if(object.userData.highlight){
      object.userData.highlight.material.clippingPlanes=cadPlanes;
      object.userData.highlight.material.needsUpdate=true;
    }
  }
  for(const object of sampleObjects){
    object.material.clippingPlanes=samplePlanes;
    object.material.needsUpdate=true;
  }
  for(const object of glyphObjects){
    object.material.clippingPlanes=samplePlanes;
    object.material.needsUpdate=true;
  }
}
function setClippingPlane(config){
  clipConfig={...clipConfig,...config};
  applyClippingPlanes();
}
function applyTranslationPreview(){
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
  if(bboxHelper)objectMatrix(bboxHelper,geometryMatrix);
  objectMatrix(grid,geometryMatrix.clone().multiply(baseGridMatrix));
  objectMatrix(axes,geometryMatrix.clone().multiply(baseAxesMatrix));
  for(const object of sampleObjects){
    const affected=whole||translationPreview?.regions.includes(object.userData.label);
    objectMatrix(object,affected?matrix:new THREE.Matrix4());
  }
  for(const object of glyphObjects){
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
function zoomToBox(box){
  if(box.isEmpty())return false;
  gizmo.cancel();stopMotion();
  const targetCenter=box.getCenter(new THREE.Vector3());
  const targetRadius=Math.max(box.getSize(new THREE.Vector3()).length()*0.6,baseRadius*0.04,1e-5);
  let dir=camera.position.clone().sub(controls.target);
  if(dir.lengthSq()<1e-12)dir.set(1,0.7,1);
  dir.normalize();
  controls.target.copy(targetCenter);
  const distance=targetRadius/Math.sin(THREE.MathUtils.degToRad(camera.fov/2))/Math.min(1,camera.aspect);
  camera.position.copy(targetCenter).addScaledVector(dir,distance);
  camera.near=Math.max(distance/10000,1e-8);camera.far=Math.max(distance*100,baseRadius*100);
  camera.updateProjectionMatrix();controls.update();
  return true;
}
function zoomToSelection(){
  const box=new THREE.Box3();
  for(const object of objects){
    if(selected.has(key(object)))box.expandByObject(object);
  }
  if(box.isEmpty()){
    for(const object of sampleObjects){
      if(object.visible)box.expandByObject(object);
    }
  }
  return zoomToBox(box);
}
function zoomToSample(label){
  const box=new THREE.Box3();
  for(const object of sampleObjects){
    if(!label||object.userData.label===label)box.expandByObject(object);
  }
  return zoomToBox(box);
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
  for(const id of ['colorbar-hud','bbox-hud']){
    const el=document.getElementById(id);
    if(el)el.classList.toggle('dark',dark);
  }
  palette={face:dark?0x6196ad:0x75a8b5,edge:dark?0xd0e4ec:0x304650};recolor();
}
function loadGeometry(data){
  translationPreview=null;previewOffset.set(0,0,0);previewMatrix.identity();
  for(const object of objects){scene.remove(object);object.traverse(part=>{part.geometry?.dispose();part.material?.dispose();});}
  if(bboxHelper){scene.remove(bboxHelper);bboxHelper.geometry?.dispose();bboxHelper.material?.dispose();bboxHelper=null;}
  objects=[];faces=[];edges=[];selected.clear();hover=null;down=null;documentId=data?.document??null;
  coordUnits=(data?.coordinate_units?.unit&&data.coordinate_units.unit!=='unitless')?data.coordinate_units.unit:'';
  if(!data){center.set(0,0,0);radius=15;geomBoxMin.set(-1,-1,-1);geomBoxMax.set(1,1,1);updateBboxHud();grid.position.set(0,0,0);grid.scale.setScalar(1);axes.position.set(0,0,0);axes.scale.setScalar(1);rememberGeometryFrame();orient(0);return;}
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
      const highlight=new LineSegments2(new LineSegmentsGeometry().setPositions(entity.positions),
        new LineMaterial({color:0xe89a34,linewidth:6,worldUnits:false,depthTest:true,depthWrite:false}));
      highlight.visible=false;
      object.add(highlight);object.userData.highlight=highlight;
    }
    objects.push(object);scene.add(object);box.expandByObject(object);
  }
  geomBoxMin.copy(box.min);geomBoxMax.copy(box.max);
  bboxHelper=new THREE.Box3Helper(box,0x3786bd);
  bboxHelper.visible=showBoundingBox;
  scene.add(bboxHelper);
  updateBboxHud();
  box.getCenter(center);radius=Math.max(box.getSize(new THREE.Vector3()).length()*.6,1e-6);
  grid.position.copy(center);grid.position.z=box.min.z-radius*.005;grid.scale.setScalar(radius/8);
  axes.position.copy(box.min);axes.scale.setScalar(radius/8);
  setShadingMode(shadingMode);
  applyClippingPlanes();
  rememberGeometryFrame();orient(data.dimension===2?1:0);recolor();
}
function setMode(value){
  mode=value==='edge'?'edge':'face';hover=null;down=null;recolor();
  renderer.domElement.style.cursor=mode==='face'?'crosshair':'cell';
}
function setSelection(values){selected=new Set(values.map(([d,t])=>`${d}:${t}`));recolor();}
function setRaycasterFromScreen(ndc){
  if(projectionMode==='orthographic'){
    raycaster.ray.origin.set(ndc.x,ndc.y,(camera.near+camera.far)/(camera.near-camera.far)).unproject(camera);
    raycaster.ray.direction.set(0,0,-1).transformDirection(camera.matrixWorld);
  }else{
    raycaster.setFromCamera(ndc,camera);
  }
}
function pickAt(x,y){
  const rect=renderer.domElement.getBoundingClientRect();
  scene.updateMatrixWorld(true);camera.updateMatrixWorld(true);
  const visibleFaces=faces.filter(o=>o.visible);
  const frontSurface=(sx,sy)=>{
    setRaycasterFromScreen(new THREE.Vector2((sx-rect.left)/rect.width*2-1,1-(sy-rect.top)/rect.height*2));
    return raycaster.intersectObjects(visibleFaces,false).find(hit=>{
      if(activeClipPlane&&activeClipPlane.distanceToPoint(hit.point)<0)return false;
      const z=hit.point.clone().project(camera).z;return z>=-1&&z<=1;
    });
  };
  if(mode==='face'){
    const hit=frontSurface(x,y);
    return hit?{object:hit.object,point:hit.point}:null;
  }
  const candidates=[];
  for(const object of edges){
    if(!object.visible)continue;
    const positions=object.geometry.attributes.position;
    for(let i=0;i+1<positions.count;i+=2){
      let a=new THREE.Vector3().fromBufferAttribute(positions,i).applyMatrix4(object.matrixWorld).applyMatrix4(camera.matrixWorldInverse);
      let b=new THREE.Vector3().fromBufferAttribute(positions,i+1).applyMatrix4(object.matrixWorld).applyMatrix4(camera.matrixWorldInverse);
      const da=-a.z,db=-b.z,delta=db-da;
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
      const u=projectionMode==='orthographic'?t:(t/(-b.z))/((1-t)/(-a.z)+t/(-b.z));
      const point=a.clone().lerp(b,u).applyMatrix4(camera.matrixWorld);
      if(activeClipPlane&&activeClipPlane.distanceToPoint(point)<0)continue;
      candidates.push({object,point,sx,sy,pixels,distance:point.distanceTo(camera.position)});
    }
  }
  candidates.sort((a,b)=>a.pixels-b.pixels||a.distance-b.distance);
  for(const hit of candidates){
    const surface=frontSurface(hit.sx,hit.sy);
    const epsilon=Math.max(hit.distance,camera.near)*1e-6;
    if(!surface||hit.distance<=surface.distance+epsilon)return {object:hit.object,point:hit.point};
  }
  return null;
}
function pickSamplePointAt(x,y){
  if(!sampleStyle.visible||!sampleObjects.length)return null;
  const rect=renderer.domElement.getBoundingClientRect();
  scene.updateMatrixWorld(true);camera.updateMatrixWorld(true);
  let best=null;
  const pt=new THREE.Vector3(),ndc=new THREE.Vector3();
  const threshold=Math.max(6,sampleStyle.size*0.75);
  for(const obj of sampleObjects){
    if(!obj.visible)continue;
    const pos=obj.geometry.attributes.position;
    for(let i=0;i<pos.count;i++){
      pt.fromBufferAttribute(pos,i).applyMatrix4(obj.matrixWorld);
      if(activeClipPlane&&clipConfig.clipSamples&&activeClipPlane.distanceToPoint(pt)<0)continue;
      ndc.copy(pt).project(camera);
      if(ndc.z<-1||ndc.z>1)continue;
      const sx=rect.left+(ndc.x+1)*rect.width/2,sy=rect.top+(1-ndc.y)*rect.height/2;
      const pixels=Math.hypot(x-sx,y-sy);
      if(pixels<=threshold&&(!best||pixels<best.pixels)){
        best={label:obj.userData.label,index:i,point:pt.clone(),pixels};
      }
    }
  }
  return best;
}
renderer.domElement.addEventListener('pointerdown',event=>{if(event.button===0)down={x:event.clientX,y:event.clientY,moved:false};});
renderer.domElement.addEventListener('pointermove',event=>{
  if(down&&Math.hypot(event.clientX-down.x,event.clientY-down.y)>4)down.moved=true;
  if(event.buttons)return;
  const sampleHit=pickSamplePointAt(event.clientX,event.clientY);
  if(sampleHit){
    window.geoBridge?.hoveredPoint(documentId,sampleHit.label,sampleHit.index,sampleHit.point.x,sampleHit.point.y,sampleHit.point.z);
  }
  const hit=pickAt(event.clientX,event.clientY);
  const object=hit?.object||null;
  const nextHover=object?key(object):null;
  if(nextHover!==hover){
    hover=nextHover;recolor();
  }
  if(!sampleHit&&hit?.point&&object){
    window.geoBridge?.hovered(documentId,object.userData.dimension,object.userData.tag,hit.point.x,hit.point.y,hit.point.z);
  }
});
renderer.domElement.addEventListener('pointerleave',()=>{hover=null;recolor();});
renderer.domElement.addEventListener('pointercancel',()=>{down=null;});
renderer.domElement.addEventListener('pointerup',event=>{
  const start=down;down=null;
  if(event.button!==0||!start||start.moved||Math.hypot(event.clientX-start.x,event.clientY-start.y)>4)return;
  const hit=pickAt(event.clientX,event.clientY);
  const object=hit?.object||null;
  window.geoBridge?.picked(documentId,object?.userData.dimension||0,object?.userData.tag||0,event.ctrlKey||event.metaKey||event.shiftKey);
});
const pointCanvas=document.createElement('canvas');pointCanvas.width=pointCanvas.height=64;
const pointContext=pointCanvas.getContext('2d');
pointContext.fillStyle='#151515';pointContext.beginPath();pointContext.arc(32,32,30,0,Math.PI*2);pointContext.fill();
pointContext.fillStyle='#ffffff';pointContext.beginPath();pointContext.arc(32,32,23,0,Math.PI*2);pointContext.fill();
const pointTexture=new THREE.CanvasTexture(pointCanvas);pointTexture.colorSpace=THREE.SRGBColorSpace;
let sampleStyle={
  size:7,visible:true,through:true,hiddenLabels:[],isolatedLabel:null,colours:{},
  colorBy:'dataset',colormap:'viridis',scalarFieldLabel:'',vectorMode:'off',glyphScale:1.0
};
function buildArrowSegments(coords,vectors,arrowLen){
  const n=Math.min(coords.length,vectors.length);
  const buf=new Float32Array(n*18);
  let k=0;
  for(let i=0;i<n;i++){
    const p=coords[i],v=vectors[i];
    const vx=v[0],vy=v[1],vz=v[2];
    const vlen=Math.hypot(vx,vy,vz);
    if(vlen<1e-9)continue;
    const dx=vx/vlen*arrowLen,dy=vy/vlen*arrowLen,dz=vz/vlen*arrowLen;
    const tx=p[0]+dx,ty=p[1]+dy,tz=p[2]+dz;
    buf[k++]=p[0];buf[k++]=p[1];buf[k++]=p[2];
    buf[k++]=tx;buf[k++]=ty;buf[k++]=tz;
    let ox=-dy,oy=dx,oz=0;
    let olen=Math.hypot(ox,oy,oz);
    if(olen<1e-6){ox=0;oy=-dz;oz=dy;olen=Math.hypot(ox,oy,oz);}
    ox=ox/olen*arrowLen*0.18;oy=oy/olen*arrowLen*0.18;oz=oz/olen*arrowLen*0.18;
    const bx=tx-dx*0.28,by=ty-dy*0.28,bz=tz-dz*0.28;
    buf[k++]=tx;buf[k++]=ty;buf[k++]=tz;
    buf[k++]=bx+ox;buf[k++]=by+oy;buf[k++]=bz+oz;
    buf[k++]=tx;buf[k++]=ty;buf[k++]=tz;
    buf[k++]=bx-ox;buf[k++]=by-oy;buf[k++]=bz-oz;
  }
  return buf.subarray(0,k);
}
function rebuildGlyphs(){
  for(const g of glyphObjects){scene.remove(g);g.geometry.dispose();g.material.dispose();}
  glyphObjects=[];
  if(sampleStyle.vectorMode==='off'||!sampleObjects.length)return;
  const arrowLen=Math.max(baseRadius,1e-3)*0.09*Math.max(0.1,Number(sampleStyle.glyphScale)||1.0);
  for(const obj of sampleObjects){
    const b=obj.userData.batchData;
    if(!b||!b.coordinates?.length)continue;
    const specs=[];
    if(b.normals?.length===b.coordinates.length)specs.push([b.normals,0xf0a830]);
    if(b.partner_normals?.length===b.coordinates.length)specs.push([b.partner_normals,0xe05a47]);
    if(sampleStyle.vectorMode==='darboux'){
      if(b.tangent_u?.length===b.coordinates.length)specs.push([b.tangent_u,0x29b6f6]);
      if(b.tangent_v?.length===b.coordinates.length)specs.push([b.tangent_v,0x66bb6a]);
    }
    for(const [vecs,hexColor] of specs){
      const segs=buildArrowSegments(b.coordinates,vecs,arrowLen);
      if(!segs.length)continue;
      const geom=new THREE.BufferGeometry();
      geom.setAttribute('position',new THREE.BufferAttribute(segs,3));
      const mat=new THREE.LineBasicMaterial({color:hexColor,depthTest:!sampleStyle.through});
      const lines=new THREE.LineSegments(geom,mat);
      lines.userData={label:b.label,segmentCount:segs.length/6};
      lines.renderOrder=3;
      lines.visible=obj.visible;
      scene.add(lines);
      glyphObjects.push(lines);
    }
  }
  applyClippingPlanes();
  applyTranslationPreview();
}
function updateScalarColorsAndHud(){
  const hud=document.getElementById('colorbar-hud');
  const useScalar=sampleStyle.colorBy&&sampleStyle.colorBy!=='dataset';
  let minVal=Infinity,maxVal=-Infinity,hasScalar=false;
  if(useScalar){
    for(const obj of sampleObjects){
      if(!obj.visible)continue;
      const vals=obj.userData.batchData?.scalars;
      if(Array.isArray(vals)&&vals.length===obj.geometry.attributes.position.count){
        for(const v of vals){
          if(Number.isFinite(v)){if(v<minVal)minVal=v;if(v>maxVal)maxVal=v;hasScalar=true;}
        }
      }
    }
  }
  if(!useScalar||!hasScalar){
    scalarRange=null;
    if(hud)hud.style.display='none';
    for(const obj of sampleObjects){
      obj.material.vertexColors=false;
      obj.material.color.set(sampleStyle.colours[obj.userData.label]??obj.userData.defaultColour);
      obj.material.needsUpdate=true;
    }
    return;
  }
  scalarRange=[minVal,maxVal];
  const span=maxVal-minVal;
  const cmap=sampleStyle.colormap||'viridis';
  for(const obj of sampleObjects){
    const vals=obj.userData.batchData?.scalars;
    const count=obj.geometry.attributes.position.count;
    if(Array.isArray(vals)&&vals.length===count){
      const rgb=new Float32Array(count*3);
      for(let i=0,j=0;i<count;i++,j+=3){
        const t=span>1e-15?(vals[i]-minVal)/span:0.5;
        const c=evalColormap(cmap,t);
        rgb[j]=c[0];rgb[j+1]=c[1];rgb[j+2]=c[2];
      }
      obj.geometry.setAttribute('color',new THREE.BufferAttribute(rgb,3));
      obj.material.vertexColors=true;
      obj.material.color.set(0xffffff);
      obj.material.needsUpdate=true;
    }else{
      obj.material.vertexColors=false;
      obj.material.color.set(sampleStyle.colours[obj.userData.label]??obj.userData.defaultColour);
      obj.material.needsUpdate=true;
    }
  }
  if(hud){
    const fmt=v=>Math.abs(v)>=1e4||(Math.abs(v)>0&&Math.abs(v)<1e-3)?v.toExponential(2):Number(v.toPrecision(4)).toString();
    document.getElementById('colorbar-title').textContent=sampleStyle.scalarFieldLabel||sampleStyle.colorBy;
    document.getElementById('colorbar-gradient').style.background=COLORMAP_CSS[cmap]||COLORMAP_CSS.viridis;
    document.getElementById('colorbar-min').textContent=fmt(minVal);
    document.getElementById('colorbar-mid').textContent=fmt(0.5*(minVal+maxVal));
    document.getElementById('colorbar-max').textContent=fmt(maxVal);
    hud.style.display='block';
  }
}
function setSampleStyle(config){
  const prevVec=sampleStyle.vectorMode,prevScale=sampleStyle.glyphScale;
  sampleStyle={...sampleStyle,...config};
  for(const object of sampleObjects){
    object.material.size=sampleStyle.size;
    object.material.depthTest=!sampleStyle.through;
    object.visible=sampleStyle.visible&&!sampleStyle.hiddenLabels.includes(object.userData.label)&&(!sampleStyle.isolatedLabel||sampleStyle.isolatedLabel===object.userData.label);
  }
  updateScalarColorsAndHud();
  if(prevVec!==sampleStyle.vectorMode||prevScale!==sampleStyle.glyphScale){
    rebuildGlyphs();
  }else{
    for(const g of glyphObjects){
      g.material.depthTest=!sampleStyle.through;
      g.visible=sampleStyle.visible&&!sampleStyle.hiddenLabels.includes(g.userData.label)&&(!sampleStyle.isolatedLabel||sampleStyle.isolatedLabel===g.userData.label);
    }
  }
}
function setSamples(config){
  for(const object of sampleObjects){scene.remove(object);object.geometry.dispose();object.material.dispose();}
  sampleObjects=[];
  const colors=[0xfff6dc,0x83e5ed,0xb5ef86,0xaabfff,0xffffff];
  for(const [index,batch] of config.batches.entries()){
    const geometry=new THREE.BufferGeometry(),coordinates=batch.coordinates,positions=new Float32Array(coordinates.length*3);
    for(let i=0,j=0;i<coordinates.length;i++,j+=3){
      const point=coordinates[i];
      positions[j]=point[0];positions[j+1]=point[1];positions[j+2]=point[2];
    }
    geometry.setAttribute('position',new THREE.BufferAttribute(positions,3));
    const object=new THREE.Points(geometry,new THREE.PointsMaterial({color:colors[index%colors.length],map:pointTexture,alphaTest:0.15,sizeAttenuation:false,depthWrite:false}));
    object.userData.label=batch.label;
    object.userData.defaultColour=colors[index%colors.length];
    object.userData.batchData=batch;
    object.renderOrder=2;
    scene.add(object);sampleObjects.push(object);
  }
  setSampleStyle({
    size:config.size??7,visible:config.visible,through:config.through,
    hiddenLabels:config.hiddenLabels??[],isolatedLabel:config.isolatedLabel??null,
    colours:config.colours??{},colorBy:config.colorBy??sampleStyle.colorBy,
    colormap:config.colormap??sampleStyle.colormap,
    scalarFieldLabel:config.scalarFieldLabel??sampleStyle.scalarFieldLabel,
    vectorMode:config.vectorMode??sampleStyle.vectorMode,
    glyphScale:config.glyphScale??sampleStyle.glyphScale
  });
  rebuildGlyphs();
  applyClippingPlanes();
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
  return {
    renderer:gl.getParameter(extension?extension.UNMASKED_RENDERER_WEBGL:gl.RENDERER),
    vendor:gl.getParameter(extension?extension.UNMASKED_VENDOR_WEBGL:gl.VENDOR),
    entityColours:Object.fromEntries(objects.map(o=>[key(o),`#${o.material.color.getHexString()}`])),
    version:gl.getParameter(gl.VERSION),lost:gl.isContextLost(),
    visibleFaces:faces.filter(o=>o.visible).length,visibleEdges:edges.filter(o=>o.visible).length,
    sampleColours:Object.fromEntries(sampleObjects.map(o=>[o.userData.label,`#${o.material.color.getHexString()}`])),
    preview:translationPreview,
    sampleOffsets:Object.fromEntries(sampleObjects.map(o=>[o.userData.label,o.position.toArray()])),
    geometryOffset:previewOffset.toArray(),geometryMatrix:previewMatrix.toArray(),
    sampleMatrices:Object.fromEntries(sampleObjects.map(o=>[o.userData.label,o.matrix.toArray()])),
    firstSamplePoints:Object.fromEntries(sampleObjects.map(o=>[o.userData.label,transformedPoint(o)])),
    firstGeometryPoints:Object.fromEntries(objects.map(o=>[key(o),transformedPoint(o)])),
    selectionCount:selected.size,pointSize:sampleStyle.size,sampleVisible:sampleStyle.visible,
    visibleSampleCount:sampleObjects.reduce((n,o)=>n+(o.visible?o.geometry.attributes.position.count:0),0),
    sampleCount:sampleObjects.reduce((n,o)=>n+o.geometry.attributes.position.count,0),
    shadingMode,clippingPlane:clipConfig,colorBy:sampleStyle.colorBy,colormap:sampleStyle.colormap,
    scalarRange,vectorMode:sampleStyle.vectorMode,glyphScale:sampleStyle.glyphScale,
    glyphObjectCount:glyphObjects.filter(g=>g.visible).length,
    glyphSegmentCount:glyphObjects.reduce((n,g)=>n+(g.visible?(g.userData.segmentCount||0):0),0),
    projectionMode,showBoundingBox,
    boundingBoxDimensions:[geomBoxMax.x-geomBoxMin.x,geomBoxMax.y-geomBoxMin.y,geomBoxMax.z-geomBoxMin.z]
  };
}
window.geoViewer={
  setBoundaryRegions,cameraState,setCameraState,setTranslationPreview,orient,appearance,loadGeometry,
  setMode,setSelection,setGeometryVisibility,setShadingMode,setClippingPlane,setProjectionMode,
  setBoundingBoxVisible,zoomToSelection,zoomToSample,setSamples,setSampleStyle,diagnostics,reset:()=>orient(0)
};
renderer.domElement.addEventListener('webglcontextlost',event=>{event.preventDefault();window.geoReportFailure('Graphics context lost. Restart the application.');});
addEventListener('resize',resize);resize();appearance({canvas:'#fafcfc',grid:'#e2e7e8',showGrid:false,showAxes:true});orient(0);setMode('face');
const clock=new THREE.Clock();renderer.setAnimationLoop(()=>{gizmo.update(clock.getDelta());controls.update();renderer.clear();renderer.render(scene,camera);gizmo.render();});
window.geoRendererReady=true;window.geoBridge?.ready();
