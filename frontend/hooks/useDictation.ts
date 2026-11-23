"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError } from "@/services/api";

export type DictationStatus = "idle" | "recording" | "transcribing";

const RATE = 16000;
const MAX_SECONDS = 30;

function wav(frames: Int16Array[]): Blob {
  const samples = frames.reduce((n, f) => n + f.length, 0);
  const buf = new ArrayBuffer(44 + samples * 2);
  const v = new DataView(buf);
  const text = (o: number, s: string) => [...s].forEach((c, i) => v.setUint8(o + i, c.charCodeAt(0)));
  text(0, "RIFF");
  v.setUint32(4, 36 + samples * 2, true);
  text(8, "WAVE");
  text(12, "fmt ");
  v.setUint32(16, 16, true);
  v.setUint16(20, 1, true);
  v.setUint16(22, 1, true);
  v.setUint32(24, RATE, true);
  v.setUint32(28, RATE * 2, true);
  v.setUint16(32, 2, true);
  v.setUint16(34, 16, true);
  text(36, "data");
  v.setUint32(40, samples * 2, true);
  let o = 44;
  for (const f of frames) {
    new Int16Array(buf, o, f.length).set(f);
    o += f.length * 2;
  }
  return new Blob([buf], { type: "audio/wav" });
}

/**
 * Record a spoken request with the same 16 kHz capture the live calls use, then turn it into
 * text. The text is handed back for the person to review; nothing is submitted.
 */
export function useDictation(onText: (text: string) => void) {
  const [status, setStatus] = useState<DictationStatus>("idle");
  const [error, setError] = useState<string | null>(null);
  const [seconds, setSeconds] = useState(0);
  const levelRef = useRef(0);
  const ctxRef = useRef<AudioContext | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const framesRef = useRef<Int16Array[]>([]);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const onTextRef = useRef(onText);
  useEffect(() => {
    onTextRef.current = onText;
  }, [onText]);

  const release = useCallback(() => {
    if (timerRef.current) clearInterval(timerRef.current);
    timerRef.current = null;
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
    void ctxRef.current?.close().catch(() => {});
    ctxRef.current = null;
    levelRef.current = 0;
  }, []);

  useEffect(() => release, [release]);

  const stop = useCallback(async () => {
    if (!streamRef.current) return;
    const frames = framesRef.current;
    framesRef.current = [];
    release();
    const samples = frames.reduce((n, f) => n + f.length, 0);
    if (samples < RATE / 2) {
      setStatus("idle");
      setError("Couldn't hear anything. Hold the mic button a little longer.");
      return;
    }
    setStatus("transcribing");
    try {
      const { text } = await api.transcribe(wav(frames));
      onTextRef.current(text);
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.payload.message : "Couldn't turn that into text. Try again.");
    } finally {
      setStatus("idle");
    }
  }, [release]);

  const start = useCallback(async () => {
    if (streamRef.current) return;
    setError(null);
    if (!navigator.mediaDevices?.getUserMedia) {
      setError("This browser can't record audio. Type your request instead.");
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
      const ctx = new AudioContext();
      await ctx.audioWorklet.addModule("/worklets/pcm-capture.js");
      const source = ctx.createMediaStreamSource(stream);
      const node = new AudioWorkletNode(ctx, "pcm-capture");
      framesRef.current = [];
      node.port.onmessage = (e: MessageEvent<ArrayBuffer>) => {
        const f = new Int16Array(e.data);
        framesRef.current.push(f);
        let peak = 0;
        for (let i = 0; i < f.length; i += 8) peak = Math.max(peak, Math.abs(f[i]));
        levelRef.current = levelRef.current * 0.6 + (peak / 32768) * 0.4;
      };
      source.connect(node);
      streamRef.current = stream;
      ctxRef.current = ctx;
      setSeconds(0);
      setStatus("recording");
      const began = Date.now();
      timerRef.current = setInterval(() => {
        const s = Math.floor((Date.now() - began) / 1000);
        setSeconds(s);
        if (s >= MAX_SECONDS) void stop();
      }, 250);
    } catch (e) {
      release();
      setStatus("idle");
      setError(
        (e as DOMException)?.name === "NotAllowedError"
          ? "Microphone blocked. Allow it in your browser's site settings, or type instead."
          : "Couldn't start the microphone. Type your request instead.",
      );
    }
  }, [release, stop]);

  return { status, error, seconds, levelRef, start, stop, maxSeconds: MAX_SECONDS };
}
