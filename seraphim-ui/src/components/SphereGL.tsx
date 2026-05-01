import { useEffect, useRef } from "react";
import * as THREE from "three";

interface SphereGLProps {
    state: "idle" | "listening" | "thinking" | "speaking";
    onClick: () => void;
}

const STATE_CONFIG = {
    idle:      { color: 0x64d8d0, speed: 0.003, size: 1.8,  opacity: 0.55, pulseAmp: 0.0  },
    listening: { color: 0x64d8d0, speed: 0.018, size: 2.2,  opacity: 0.85, pulseAmp: 0.12 },
    thinking:  { color: 0x64b4ff, speed: 0.006, size: 1.9,  opacity: 0.6,  pulseAmp: 0.04 },
    speaking:  { color: 0xb464ff, speed: 0.014, size: 2.0,  opacity: 0.75, pulseAmp: 0.08 },
};

export default function SphereGL({ state, onClick }: SphereGLProps) {
    const canvasRef = useRef<HTMLCanvasElement>(null);
    const stateRef  = useRef(state);
    stateRef.current = state;

    useEffect(() => {
        const canvas = canvasRef.current!;
        const W = 260, H = 260;

        // ── Renderer ──────────────────────────────────────────────
        const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
        renderer.setSize(W, H);
        renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
        renderer.setClearColor(0x000000, 0);

        // ── Scene / Camera ─────────────────────────────────────────
        const scene  = new THREE.Scene();
        const camera = new THREE.PerspectiveCamera(60, W / H, 0.1, 100);
        camera.position.z = 3.5;

        // ── Particules sur sphère ──────────────────────────────────
        const COUNT = 2400;
        const positions   = new Float32Array(COUNT * 3);
        const basePos     = new Float32Array(COUNT * 3);
        const phases      = new Float32Array(COUNT);

        for (let i = 0; i < COUNT; i++) {
            // distribution uniforme sur sphère (Fibonacci)
            const phi   = Math.acos(1 - 2 * (i + 0.5) / COUNT);
            const theta = Math.PI * (1 + Math.sqrt(5)) * i;
            const x = Math.sin(phi) * Math.cos(theta);
            const y = Math.sin(phi) * Math.sin(theta);
            const z = Math.cos(phi);
            basePos[i * 3]     = x;
            basePos[i * 3 + 1] = y;
            basePos[i * 3 + 2] = z;
            phases[i] = Math.random() * Math.PI * 2;
        }

        const geo = new THREE.BufferGeometry();
        geo.setAttribute("position", new THREE.BufferAttribute(positions.slice(), 3));

        const mat = new THREE.PointsMaterial({
            color:       0x64d8d0,
            size:        0.028,
            transparent: true,
            opacity:     0.55,
            sizeAttenuation: true,
            depthWrite:  false,
        });

        const mesh = new THREE.Points(geo, mat);
        scene.add(mesh);

        // ── Lignes de latitude légères ─────────────────────────────
        const lineMat = new THREE.LineBasicMaterial({ color: 0x64d8d0, transparent: true, opacity: 0.1 });
        [-0.6, 0, 0.6].forEach(y => {
            const r = Math.sqrt(1 - y * y);
            const pts = [];
            for (let a = 0; a <= Math.PI * 2; a += 0.15) pts.push(new THREE.Vector3(Math.cos(a) * r, y, Math.sin(a) * r));
            const lg = new THREE.BufferGeometry().setFromPoints(pts);
            scene.add(new THREE.Line(lg, lineMat));
        });

        // ── Interaction souris ─────────────────────────────────────
        const mouse = new THREE.Vector2(9999, 9999);
        const onMove = (e: MouseEvent) => {
            const rect = canvas.getBoundingClientRect();
            mouse.x =  ((e.clientX - rect.left) / W) * 2 - 1;
            mouse.y = -((e.clientY - rect.top)  / H) * 2 + 1;
        };
        const onLeave = () => { mouse.set(9999, 9999); };
        canvas.addEventListener("mousemove", onMove);
        canvas.addEventListener("mouseleave", onLeave);

        // ── Boucle d'animation ─────────────────────────────────────
        let raf: number;
        let t = 0;

        const animate = () => {
            raf = requestAnimationFrame(animate);
            t += 0.016;

            const cfg = STATE_CONFIG[stateRef.current];

            // Lerp couleur et opacité
            const target = new THREE.Color(cfg.color);
            mat.color.lerp(target, 0.05);
            mat.opacity += (cfg.opacity - mat.opacity) * 0.05;

            // Rotation
            mesh.rotation.y += cfg.speed;
            mesh.rotation.x += cfg.speed * 0.3;

            // Mise à jour positions avec pulse + interaction
            const posAttr = geo.attributes.position as THREE.BufferAttribute;
            const raycaster = new THREE.Raycaster();
            raycaster.setFromCamera(mouse, camera);

            for (let i = 0; i < COUNT; i++) {
                const bx = basePos[i * 3];
                const by = basePos[i * 3 + 1];
                const bz = basePos[i * 3 + 2];

                // Pulsation
                const pulse = 1 + Math.sin(t * 2 + phases[i]) * cfg.pulseAmp;
                const r = cfg.size / 2 * pulse;

                // Repulsion souris (en espace NDC approximatif)
                const wx = bx * r, wy = by * r, wz = bz * r;
                const proj = new THREE.Vector3(wx, wy, wz).project(camera);
                const dx = proj.x - mouse.x;
                const dy = proj.y - mouse.y;
                const dist = Math.sqrt(dx * dx + dy * dy);
                const repel = dist < 0.25 ? (0.25 - dist) * 0.35 : 0;

                posAttr.setXYZ(
                    i,
                    wx + bx * repel,
                    wy + by * repel,
                    wz + bz * repel
                );
            }
            posAttr.needsUpdate = true;

            // Scale global
            const targetScale = cfg.size / 2;
            mesh.scale.lerp(new THREE.Vector3(targetScale, targetScale, targetScale), 0.06);

            renderer.render(scene, camera);
        };
        animate();

        return () => {
            cancelAnimationFrame(raf);
            canvas.removeEventListener("mousemove", onMove);
            canvas.removeEventListener("mouseleave", onLeave);
            renderer.dispose();
            geo.dispose();
            mat.dispose();
        };
    }, []);

    return (
        <canvas
            ref={canvasRef}
            width={260}
            height={260}
            onClick={onClick}
            style={{ cursor: "pointer", display: "block" }}
            aria-label="Seraphim orb"
        />
    );
}