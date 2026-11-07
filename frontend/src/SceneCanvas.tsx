import { useLayoutEffect, useRef, useState } from 'react';
import Konva from 'konva';
import { Arrow, Circle, Group, Layer, Line, Rect, Stage, Text } from 'react-konva';
import { useReducedMotion } from 'motion/react';
import type { Scene, SceneObject } from './types';

const width = 800;
const height = 450;
const palette = {
  neutral: { fill: '#e9efed', stroke: '#49665d' },
  accent: { fill: '#dce9ff', stroke: '#2959a6' },
  success: { fill: '#def2e3', stroke: '#2f7149' },
  warning: { fill: '#ffedcc', stroke: '#92501a' },
};
type ObjectState = SceneObject & { visible: boolean };

function sceneFrame(scene: Scene, index: number) {
  const objects = new Map<string, ObjectState>(scene.objects.map(object =>
    [object.id, { ...object, visible: true }]));
  for (const step of scene.steps.slice(0, index + 1)) {
    for (const change of step.changes) {
      const object = objects.get(change.id)!;
      object.x = change.x ?? object.x;
      object.y = change.y ?? object.y;
      object.tone = change.tone ?? object.tone;
      object.visible = change.visible ?? object.visible;
      if (change.label != null) object.label = change.label;
    }
  }
  return [...objects.values()];
}

function SceneShape({ object, duration }: { object: ObjectState; duration: number }) {
  const group = useRef<Konva.Group>(null);
  const initialized = useRef(false);
  const { x, y, visible } = object;
  useLayoutEffect(() => {
    const node = group.current!;
    const attrs = { x, y, opacity: visible ? 1 : 0 };
    if (!initialized.current || duration === 0) {
      initialized.current = true;
      node.setAttrs(attrs);
      return;
    }
    const tween = new Konva.Tween({ node, ...attrs, duration, easing: Konva.Easings.EaseInOut });
    tween.play();
    return () => { tween.destroy(); };
  }, [x, y, visible, duration]);

  const colors = palette[object.tone];
  const caption = 'label' in object && object.label;
  const textStyle = { fontFamily: 'system-ui, sans-serif', fontSize: 18, ellipsis: true,
    fill: '#1c332b', lineHeight: 1.15 };
  return <Group ref={group}>
    {object.kind === 'rect' && <>
      <Rect width={object.width} height={object.height} cornerRadius={7} {...colors} strokeWidth={2} />
      {caption && <Text {...textStyle} text={caption} width={object.width} height={object.height}
        padding={5} align="center" verticalAlign="middle" />}
    </>}
    {object.kind === 'circle' && <>
      <Circle radius={object.radius} {...colors} strokeWidth={2} />
      {caption && <Text {...textStyle} text={caption} x={-object.radius} y={-object.radius}
        width={object.radius * 2} height={object.radius * 2} padding={5} align="center" verticalAlign="middle" />}
    </>}
    {object.kind === 'text' && <Text {...textStyle} text={object.label} width={object.width}
      height={height - object.y} fill={colors.stroke} />}
    {object.kind === 'line' && <Line points={[0, 0, object.dx, object.dy]} stroke={colors.stroke} strokeWidth={3} />}
    {object.kind === 'arrow' && <Arrow points={[0, 0, object.dx, object.dy]} stroke={colors.stroke}
      fill={colors.stroke} strokeWidth={3} pointerLength={12} pointerWidth={10} />}
    {(object.kind === 'line' || object.kind === 'arrow') && caption && <Text {...textStyle}
      text={caption} width={160} height={22} align="center" wrap="none" fontSize={14}
      x={Math.max(0, Math.min(width - 160, x + object.dx / 2 - 80)) - x}
      y={Math.max(0, Math.min(height - 22, y + object.dy / 2 - 24)) - y} />}
  </Group>;
}

export default function SceneCanvas({ scene, index, speed }: { scene: Scene; index: number; speed: number }) {
  const host = useRef<HTMLDivElement>(null);
  const [displayWidth, setDisplayWidth] = useState(0);
  const reducedMotion = useReducedMotion();
  useLayoutEffect(() => {
    const element = host.current!;
    const resize = () => setDisplayWidth(element.clientWidth);
    const observer = new ResizeObserver(resize);
    observer.observe(element);
    resize();
    return () => observer.disconnect();
  }, []);
  const scale = displayWidth / width;
  const objects = sceneFrame(scene, index);
  const description = objects.flatMap(object =>
    object.visible && 'label' in object && object.label ? [object.label] : []).join('. ');
  return <div ref={host} className="scene-canvas" role="img"
    aria-label={description ? scene.title + '. ' + description : scene.title}>
    {displayWidth > 0 && <Stage width={displayWidth} height={height * scale} scaleX={scale} scaleY={scale}>
      <Layer listening={false}>
        {objects.map(object => <SceneShape key={object.id} object={object}
          duration={reducedMotion ? 0 : 0.36 / speed} />)}
      </Layer>
    </Stage>}
  </div>;
}
