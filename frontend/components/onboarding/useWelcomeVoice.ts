"use client";

import { useCallback, useEffect, useRef, useState } from "react";

interface Line {
  src: string;
  text: string;
  duration: number;
}

const GAP_MS = 380;
const READ_MS_PER_WORD = 330;

/**
 * Plays the welcome call's three pre-rendered lines in the agent's voice, reporting which
 * line is being said and how far through it is, plus a live level for the orb and room light.
 * Without audio (blocked, failed, muted device) the same timeline runs at reading pace.
 */
export function useWelcomeVoice() {
  const levelRef = useRef(0);
  const [lines, setLines] = useState<Line[]>([]);
  const [line, setLine] = useState(-1);
  const [progress, setProgress] = useState(0);
  const [done, setDone] = useState(false);
  const [muted, setMutedState] = useState(false);
  const ctxRef = useRef<AudioContext | null>(null);
  const gainRef = useRef<GainNode | null>(null);
  const sourceRef = useRef<AudioBufferSourceNode | null>(null);
  const cancelled = useRef(false);
  const raf = useRef(0);

  useEffect(() => {
    cancelled.current = false;
    fetch("/welcome/welcome.json")
      .then((r) => r.json())
      .then((d: { lines: Line[] }) => setLines(d.lines))
      .catch(() => setLines([]));
    return () => {
      cancelled.current = true;
      cancelAnimationFrame(raf.current);
      sourceRef.current?.stop();
      void ctxRef.current?.close();
    };
  }, []);

  const setMuted = useCallback((m: boolean) => {
    setMutedState(m);
    if (gainRef.current) gainRef.current.gain.value = m ? 0 : 1;
  }, []);

  const runTimed = (ms: number) =>
    new Promise<void>((resolve) => {
      const start = performance.now();
      const tick = () => {
        if (cancelled.current) return resolve();
        const p = Math.min(1, (performance.now() - start) / ms);
        setProgress(p);
        levelRef.current = 0;
        if (p >= 1) return resolve();
        raf.current = requestAnimationFrame(tick);
      };
      tick();
    });

  const playClip = async (ctx: AudioContext, l: Line) => {
    const data = await (await fetch(l.src)).arrayBuffer();
    const buffer = await ctx.decodeAudioData(data);
    const analyser = ctx.createAnalyser();
    analyser.fftSize = 512;
    const samples = new Float32Array(analyser.fftSize);
    const source = ctx.createBufferSource();
    source.buffer = buffer;
    source.connect(analyser).connect(gainRef.current!);
    sourceRef.current = source;
    await new Promise<void>((resolve) => {
      // Words follow the wall clock, so a stalled or silent audio device can't freeze the call.
      const start = performance.now();
      const ms = buffer.duration * 1000;
      let finished = false;
      const finish = () => {
        if (!finished) {
          finished = true;
          resolve();
        }
      };
      source.onended = finish;
      source.start();
      const tick = () => {
        if (cancelled.current) return finish();
        analyser.getFloatTimeDomainData(samples);
        let sum = 0;
        for (const v of samples) sum += v * v;
        levelRef.current = Math.min(1, Math.sqrt(sum / samples.length) * 5);
        const elapsed = performance.now() - start;
        setProgress(Math.min(1, elapsed / ms));
        if (elapsed > ms + 400) return finish();
        raf.current = requestAnimationFrame(tick);
      };
      tick();
    });
    cancelAnimationFrame(raf.current);
    levelRef.current = 0;
    setProgress(1);
  };

  /** Call from the Answer tap: a user gesture, so the browser lets it speak. */
  const play = useCallback(async () => {
    cancelled.current = false;
    let ctx: AudioContext | null = null;
    try {
      ctx = new AudioContext();
      const gain = ctx.createGain();
      gain.gain.value = muted ? 0 : 1;
      gain.connect(ctx.destination);
      ctxRef.current = ctx;
      gainRef.current = gain;
      await ctx.resume();
    } catch {
      ctx = null;
    }
    const script = lines.length ? lines : [];
    for (let i = 0; i < script.length; i++) {
      if (cancelled.current) return;
      setLine(i);
      setProgress(0);
      try {
        if (!ctx) throw new Error("no audio");
        await playClip(ctx, script[i]);
      } catch {
        await runTimed(Math.max(script[i].duration * 1000, script[i].text.split(" ").length * READ_MS_PER_WORD));
      }
      await new Promise((r) => setTimeout(r, GAP_MS));
    }
    setDone(true);
  }, [lines, muted]);

  const stop = useCallback(() => {
    cancelled.current = true;
    cancelAnimationFrame(raf.current);
    try {
      sourceRef.current?.stop();
    } catch {
      /* already stopped */
    }
    levelRef.current = 0;
  }, []);

  return { lines, line, progress, done, muted, setMuted, play, stop, levelRef, ready: lines.length > 0 };
}
