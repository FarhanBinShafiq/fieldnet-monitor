"use client";

import React, { useState, useEffect } from "react";
import { 
  Radio, Activity, Server, AlertTriangle, Play, Square, 
  RotateCcw, PowerOff, ShieldAlert, Clock, RefreshCw, Eye
} from "lucide-react";

interface Gateway {
  gateway_id: string;
  name: string;
  status: string;
  status_since: string;
  command_state: string;
  coverage_class: string;
  last_heartbeat_at: string | null;
  last_qualifying_at: string | null;
  disconnected_since: string | null;
  flags: string[];
}

interface Sensor {
  sensor_id: string;
  type: string;
  lifecycle: string;
  lifecycle_reason: string | null;
  lifecycle_since: string;
  collection: string;
  coverage: string;
  quiet_checked_days: number;
  sampling_cycles_done: number;
  next_evaluation_at: string | null;
}

interface TimelineEntry {
  seq: number;
  kind: string;
  axis: string;
  from: string | null;
  to: string | null;
  effective_at: string;
  recorded_at: string;
  rule: string | null;
  evidence_ids: string[];
}

export default function FieldNetConsole() {
  const [activeTab, setActiveTab] = useState<"fleet" | "sensors">("fleet");
  const [gateways, setGateways] = useState<Gateway[]>([]);
  const [sensors, setSensors] = useState<Sensor[]>([]);
  const [loading, setLoading] = useState(false);
  
  // Modals & Inspection
  const [timelineSubject, setTimelineSubject] = useState<{ type: "gateways" | "sensors"; id: string } | null>(null);
  const [timelineEntries, setTimelineEntries] = useState<TimelineEntry[]>([]);
  const [actionModal, setActionModal] = useState<{
    type: "gateway" | "sensor";
    id: string;
    action: string;
    requiresReason: boolean;
  } | null>(null);
  const [reasonInput, setReasonInput] = useState("");
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000";

  const fetchData = async () => {
    setLoading(true);
    try {
      const [gwRes, sensorRes] = await Promise.all([
        fetch(`${API_URL}/api/v1/gateways`),
        fetch(`${API_URL}/api/v1/sensors`),
      ]);
      if (gwRes.ok) setGateways(await gwRes.json());
      if (sensorRes.ok) setSensors(await sensorRes.json());
    } catch (err: any) {
      console.error("Fetch error:", err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchData();
    const interval = setInterval(fetchData, 4000);
    return () => clearInterval(interval);
  }, []);

  const openTimeline = async (type: "gateways" | "sensors", id: string) => {
    setTimelineSubject({ type, id });
    try {
      const res = await fetch(`${API_URL}/api/v1/${type}/${id}/timeline`);
      if (res.ok) {
        setTimelineEntries(await res.json());
      }
    } catch (err) {
      console.error(err);
    }
  };

  const handleActionSubmit = async () => {
    if (!actionModal) return;
    setErrorMsg(null);
    try {
      const url = actionModal.type === "gateway"
        ? `${API_URL}/api/v1/gateways/${actionModal.id}/actions`
        : `${API_URL}/api/v1/sensors/${actionModal.id}/actions`;

      const payload: any = { action: actionModal.action };
      if (actionModal.requiresReason) {
        payload.reason = reasonInput;
      }

      const res = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });

      if (!res.ok) {
        const errData = await res.json();
        setErrorMsg(errData.detail || errData.error || "Action failed");
        return;
      }

      setActionModal(null);
      setReasonInput("");
      fetchData();
    } catch (err: any) {
      setErrorMsg(err.message);
    }
  };

  const formatDuration = (sinceIso: string) => {
    if (!sinceIso) return "-";
    const start = new Date(sinceIso).getTime();
    const now = Date.now();
    const diffSec = Math.max(0, Math.floor((now - start) / 1000));
    if (diffSec < 60) return `${diffSec}s`;
    if (diffSec < 3600) return `${Math.floor(diffSec / 60)}m`;
    if (diffSec < 86400) return `${Math.floor(diffSec / 3600)}h`;
    return `${Math.floor(diffSec / 86400)}d`;
  };

  return (
    <div className="min-h-screen bg-slate-950 p-6 font-sans">
      <header className="mb-8 flex flex-col md:flex-row md:items-center justify-between gap-4 border-b border-slate-800 pb-5">
        <div>
          <div className="flex items-center gap-3">
            <Radio className="h-8 w-8 text-sky-400" />
            <h1 className="text-2xl font-bold tracking-tight text-white">FieldNet Monitor</h1>
            <span className="rounded-full bg-sky-950 px-2.5 py-0.5 text-xs font-semibold text-sky-300 border border-sky-800">
              Live Console
            </span>
          </div>
          <p className="mt-1 text-sm text-slate-400">
            Unreliable Device Telemetry & Deterministic State Machine Engine
          </p>
        </div>

        <button
          onClick={fetchData}
          className="flex items-center gap-2 rounded-lg bg-slate-800 px-3.5 py-2 text-sm font-medium text-slate-200 hover:bg-slate-700 transition"
        >
          <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin text-sky-400" : ""}`} />
          Refresh
        </button>
      </header>

      {/* Dashboard Situation Counts */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-8">
        <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-4">
          <div className="text-xs font-medium text-slate-400">Gateways Total</div>
          <div className="mt-2 text-3xl font-bold text-white">{gateways.length}</div>
          <div className="mt-1 text-xs text-emerald-400">
            {gateways.filter((g) => g.status === "connected").length} connected
          </div>
        </div>
        <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-4">
          <div className="text-xs font-medium text-slate-400">Abnormal Gateways</div>
          <div className="mt-2 text-3xl font-bold text-amber-400">
            {gateways.filter((g) => ["stale", "disconnected", "suspended"].includes(g.status)).length}
          </div>
          <div className="mt-1 text-xs text-slate-400">stale / disconnected</div>
        </div>
        <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-4">
          <div className="text-xs font-medium text-slate-400">Sensors Active</div>
          <div className="mt-2 text-3xl font-bold text-emerald-400">
            {sensors.filter((s) => s.lifecycle === "active").length}
          </div>
          <div className="mt-1 text-xs text-slate-400">of {sensors.length} registered</div>
        </div>
        <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-4">
          <div className="text-xs font-medium text-slate-400">Sensors Inactive</div>
          <div className="mt-2 text-3xl font-bold text-rose-400">
            {sensors.filter((s) => ["dormant", "retired", "decommissioned"].includes(s.lifecycle)).length}
          </div>
          <div className="mt-1 text-xs text-slate-400">dormant / retired</div>
        </div>
      </div>

      {/* Navigation Tabs */}
      <div className="flex border-b border-slate-800 mb-6 gap-6">
        <button
          onClick={() => setActiveTab("fleet")}
          className={`pb-3 text-sm font-semibold transition border-b-2 flex items-center gap-2 ${
            activeTab === "fleet"
              ? "border-sky-400 text-sky-400"
              : "border-transparent text-slate-400 hover:text-slate-200"
          }`}
        >
          <Server className="h-4 w-4" />
          Fleet Management ({gateways.length})
        </button>
        <button
          onClick={() => setActiveTab("sensors")}
          className={`pb-3 text-sm font-semibold transition border-b-2 flex items-center gap-2 ${
            activeTab === "sensors"
              ? "border-sky-400 text-sky-400"
              : "border-transparent text-slate-400 hover:text-slate-200"
          }`}
        >
          <Activity className="h-4 w-4" />
          Sensor Network ({sensors.length})
        </button>
      </div>

      {/* Fleet Table */}
      {activeTab === "fleet" && (
        <div className="overflow-x-auto rounded-xl border border-slate-800 bg-slate-900/40">
          <table className="w-full text-left text-sm text-slate-300">
            <thead className="bg-slate-900/80 text-xs uppercase text-slate-400 border-b border-slate-800">
              <tr>
                <th className="px-5 py-3.5">Gateway</th>
                <th className="px-5 py-3.5">Status & Duration</th>
                <th className="px-5 py-3.5">Coverage Class</th>
                <th className="px-5 py-3.5">Command State</th>
                <th className="px-5 py-3.5">Heartbeat vs Evidence</th>
                <th className="px-5 py-3.5">Flags</th>
                <th className="px-5 py-3.5 text-right">Operator Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800/60 font-mono text-xs">
              {gateways.length === 0 ? (
                <tr>
                  <td colSpan={7} className="text-center py-8 text-slate-500 font-sans">
                    No gateways registered yet.
                  </td>
                </tr>
              ) : (
                gateways.map((gw) => {
                  const statusColors: any = {
                    connected: "bg-emerald-950 text-emerald-400 border-emerald-800",
                    new: "bg-sky-950 text-sky-400 border-sky-800",
                    spare: "bg-slate-800 text-slate-300 border-slate-700",
                    stale: "bg-amber-950 text-amber-400 border-amber-800",
                    disconnected: "bg-rose-950 text-rose-400 border-rose-800",
                    suspended: "bg-purple-950 text-purple-400 border-purple-800",
                    retired: "bg-gray-900 text-gray-500 border-gray-800",
                  };

                  return (
                    <tr key={gw.gateway_id} className="hover:bg-slate-900/80 transition">
                      <td className="px-5 py-4 font-sans font-medium text-white">
                        <div className="flex items-center gap-2">
                          <span>{gw.name}</span>
                          <span className="font-mono text-xs text-slate-500">({gw.gateway_id})</span>
                        </div>
                      </td>
                      <td className="px-5 py-4">
                        <span
                          className={`inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full border ${
                            statusColors[gw.status] || "bg-slate-800 text-slate-300 border-slate-700"
                          }`}
                        >
                          <span className="capitalize">{gw.status}</span>
                          <span className="opacity-70">· {formatDuration(gw.status_since)}</span>
                        </span>
                      </td>
                      <td className="px-5 py-4 uppercase font-bold text-[11px] text-slate-300">
                        {gw.coverage_class}
                      </td>
                      <td className="px-5 py-4">
                        <span className="text-slate-300">{gw.command_state}</span>
                      </td>
                      <td className="px-5 py-4 font-sans">
                        <div className="text-[11px] text-slate-400">
                          <div>Heartbeat: <span className="text-slate-200">{gw.last_heartbeat_at ? formatDuration(gw.last_heartbeat_at) + " ago" : "never"}</span></div>
                          <div>Evidence: <span className="text-slate-200">{gw.last_qualifying_at ? formatDuration(gw.last_qualifying_at) + " ago" : "never"}</span></div>
                        </div>
                      </td>
                      <td className="px-5 py-4">
                        {gw.flags.length > 0 ? (
                          gw.flags.map((flag) => (
                            <span key={flag} className="inline-flex items-center gap-1 bg-red-950 text-red-400 border border-red-800 px-2 py-0.5 rounded text-[11px]">
                              <AlertTriangle className="h-3 w-3" />
                              {flag}
                            </span>
                          ))
                        ) : (
                          <span className="text-slate-600">-</span>
                        )}
                      </td>
                      <td className="px-5 py-4 text-right font-sans">
                        <div className="flex items-center justify-end gap-1.5">
                          <button
                            onClick={() => openTimeline("gateways", gw.gateway_id)}
                            className="p-1.5 text-slate-400 hover:text-sky-400 hover:bg-slate-800 rounded transition"
                            title="Audit Timeline"
                          >
                            <Eye className="h-4 w-4" />
                          </button>
                          {gw.command_state === "running" && (
                            <button
                              onClick={() => setActionModal({ type: "gateway", id: gw.gateway_id, action: "stop", requiresReason: false })}
                              className="px-2 py-1 text-xs bg-slate-800 hover:bg-amber-900/60 text-amber-300 rounded border border-slate-700 transition"
                            >
                              Stop
                            </button>
                          )}
                          {gw.command_state === "stopped" && (
                            <button
                              onClick={() => setActionModal({ type: "gateway", id: gw.gateway_id, action: "resume", requiresReason: false })}
                              className="px-2 py-1 text-xs bg-slate-800 hover:bg-emerald-900/60 text-emerald-300 rounded border border-slate-700 transition"
                            >
                              Resume
                            </button>
                          )}
                          {gw.status !== "suspended" && gw.status !== "retired" && (
                            <button
                              onClick={() => setActionModal({ type: "gateway", id: gw.gateway_id, action: "suspend", requiresReason: true })}
                              className="px-2 py-1 text-xs bg-slate-800 hover:bg-purple-900/60 text-purple-300 rounded border border-slate-700 transition"
                            >
                              Suspend
                            </button>
                          )}
                          {gw.status === "suspended" && (
                            <button
                              onClick={() => setActionModal({ type: "gateway", id: gw.gateway_id, action: "unsuspend", requiresReason: false })}
                              className="px-2 py-1 text-xs bg-slate-800 hover:bg-purple-900/60 text-purple-300 rounded border border-slate-700 transition"
                            >
                              Unsuspend
                            </button>
                          )}
                        </div>
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>
      )}

      {/* Sensors Table */}
      {activeTab === "sensors" && (
        <div className="overflow-x-auto rounded-xl border border-slate-800 bg-slate-900/40">
          <table className="w-full text-left text-sm text-slate-300">
            <thead className="bg-slate-900/80 text-xs uppercase text-slate-400 border-b border-slate-800">
              <tr>
                <th className="px-5 py-3.5">Sensor ID</th>
                <th className="px-5 py-3.5">Type</th>
                <th className="px-5 py-3.5">Lifecycle</th>
                <th className="px-5 py-3.5">Reason</th>
                <th className="px-5 py-3.5">Collection Status</th>
                <th className="px-5 py-3.5">Coverage</th>
                <th className="px-5 py-3.5">Next Evaluation</th>
                <th className="px-5 py-3.5 text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800/60 font-mono text-xs">
              {sensors.length === 0 ? (
                <tr>
                  <td colSpan={8} className="text-center py-8 text-slate-500 font-sans">
                    No sensors registered yet.
                  </td>
                </tr>
              ) : (
                sensors.map((sensor) => {
                  const lifecycleBadge: any = {
                    active: "bg-emerald-950 text-emerald-400 border-emerald-800",
                    pending: "bg-sky-950 text-sky-400 border-sky-800",
                    dormant: "bg-amber-950 text-amber-400 border-amber-800",
                    sampling: "bg-blue-950 text-blue-400 border-blue-800",
                    retired: "bg-rose-950 text-rose-400 border-rose-800",
                    decommissioned: "bg-gray-900 text-gray-500 border-gray-800",
                  };

                  const collectionBadge: any = {
                    readings: "text-emerald-400",
                    no_readings: "text-amber-400",
                    not_checked: "text-yellow-500 font-bold bg-yellow-950/60 px-2 py-0.5 rounded border border-yellow-800",
                    collection_stopped: "text-orange-400 font-bold bg-orange-950/60 px-2 py-0.5 rounded border border-orange-800",
                    could_not_read: "text-rose-400",
                    timed_out: "text-rose-500",
                  };

                  return (
                    <tr key={sensor.sensor_id} className="hover:bg-slate-900/80 transition">
                      <td className="px-5 py-4 font-bold text-white">{sensor.sensor_id}</td>
                      <td className="px-5 py-4 capitalize font-sans text-slate-300">{sensor.type}</td>
                      <td className="px-5 py-4">
                        <span className={`px-2.5 py-0.5 rounded-full border capitalize ${lifecycleBadge[sensor.lifecycle] || "bg-slate-800 text-slate-300"}`}>
                          {sensor.lifecycle}
                        </span>
                      </td>
                      <td className="px-5 py-4 font-sans text-slate-400">
                        {sensor.lifecycle_reason ? (
                          <span className="text-rose-300">{sensor.lifecycle_reason}</span>
                        ) : (
                          <span className="text-slate-600">-</span>
                        )}
                      </td>
                      <td className="px-5 py-4">
                        <span className={collectionBadge[sensor.collection] || "text-slate-300"}>
                          {sensor.collection}
                        </span>
                      </td>
                      <td className="px-5 py-4 uppercase font-bold text-[11px] text-slate-300">
                        {sensor.coverage}
                      </td>
                      <td className="px-5 py-4 text-slate-400 font-sans">
                        {sensor.next_evaluation_at ? (
                          <span className="inline-flex items-center gap-1 text-sky-400">
                            <Clock className="h-3 w-3" />
                            {new Date(sensor.next_evaluation_at).toLocaleString()}
                          </span>
                        ) : (
                          <span className="text-slate-600 font-bold">paused / null</span>
                        )}
                      </td>
                      <td className="px-5 py-4 text-right font-sans">
                        <div className="flex items-center justify-end gap-1.5">
                          <button
                            onClick={() => openTimeline("sensors", sensor.sensor_id)}
                            className="p-1.5 text-slate-400 hover:text-sky-400 hover:bg-slate-800 rounded transition"
                            title="Audit Timeline"
                          >
                            <Eye className="h-4 w-4" />
                          </button>
                          {sensor.lifecycle !== "decommissioned" && (
                            <button
                              onClick={() => setActionModal({ type: "sensor", id: sensor.sensor_id, action: "decommission", requiresReason: true })}
                              className="px-2 py-1 text-xs bg-slate-800 hover:bg-red-900/60 text-red-300 rounded border border-slate-700 transition"
                            >
                              Decommission
                            </button>
                          )}
                        </div>
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>
      )}

      {/* Action Dialog Modal */}
      {actionModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/75 p-4 backdrop-blur-sm">
          <div className="w-full max-w-md rounded-xl border border-slate-800 bg-slate-900 p-6 shadow-2xl">
            <h3 className="text-lg font-bold text-white capitalize">
              Execute {actionModal.action} on {actionModal.id}
            </h3>
            <p className="mt-1 text-sm text-slate-400">
              Confirm this action. An audit record will be permanently appended to the timeline.
            </p>

            {actionModal.requiresReason && (
              <div className="mt-4">
                <label className="block text-xs font-semibold text-slate-300 uppercase">Reason (Required):</label>
                <textarea
                  value={reasonInput}
                  onChange={(e) => setReasonInput(e.target.value)}
                  placeholder="Provide an operator explanation..."
                  className="mt-1.5 w-full rounded-lg border border-slate-700 bg-slate-950 p-2.5 text-sm text-slate-200 focus:border-sky-500 focus:outline-none"
                  rows={3}
                />
              </div>
            )}

            {errorMsg && (
              <div className="mt-3 rounded bg-red-950/80 border border-red-800 p-2 text-xs text-red-300">
                {errorMsg}
              </div>
            )}

            <div className="mt-6 flex justify-end gap-3">
              <button
                onClick={() => setActionModal(null)}
                className="px-4 py-2 text-sm text-slate-400 hover:text-white"
              >
                Cancel
              </button>
              <button
                onClick={handleActionSubmit}
                disabled={actionModal.requiresReason && !reasonInput.trim()}
                className="rounded-lg bg-sky-600 px-4 py-2 text-sm font-semibold text-white hover:bg-sky-500 disabled:opacity-50"
              >
                Confirm Action
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Timeline Modal */}
      {timelineSubject && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/75 p-4 backdrop-blur-sm">
          <div className="w-full max-w-3xl max-h-[85vh] flex flex-col rounded-xl border border-slate-800 bg-slate-900 shadow-2xl">
            <div className="flex items-center justify-between border-b border-slate-800 p-5">
              <h3 className="text-lg font-bold text-white">
                Immutable Audit Timeline for {timelineSubject.id}
              </h3>
              <button
                onClick={() => setTimelineSubject(null)}
                className="text-slate-400 hover:text-white text-lg font-bold"
              >
                ✕
              </button>
            </div>

            <div className="overflow-y-auto p-5 space-y-3 font-mono text-xs">
              {timelineEntries.length === 0 ? (
                <div className="text-center py-6 text-slate-500 font-sans">No timeline entries yet.</div>
              ) : (
                timelineEntries.map((e) => (
                  <div key={e.seq} className="rounded-lg border border-slate-800 bg-slate-950/70 p-3.5 hover:border-slate-700 transition">
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-2">
                        <span className="rounded bg-slate-800 px-1.5 py-0.5 text-[10px] text-slate-400">#{e.seq}</span>
                        <span className="font-bold text-sky-400 uppercase">{e.kind}</span>
                        <span className="text-slate-400">[{e.axis}]</span>
                      </div>
                      <span className="text-[11px] text-slate-500">{new Date(e.effective_at).toISOString()}</span>
                    </div>

                    <div className="mt-2 text-sm text-slate-200">
                      <span className="text-slate-400">{e.from || "null"}</span>
                      <span className="text-sky-400 mx-2">→</span>
                      <span className="font-bold text-white">{e.to || "null"}</span>
                    </div>

                    {(e.rule || (e.evidence_ids && e.evidence_ids.length > 0)) && (
                      <div className="mt-2 text-[11px] text-slate-400 border-t border-slate-900 pt-2 flex flex-wrap gap-3">
                        {e.rule && <div>Rule: <span className="text-slate-300">{e.rule}</span></div>}
                        {e.evidence_ids && e.evidence_ids.length > 0 && (
                          <div>Evidence: <span className="text-slate-300">{e.evidence_ids.join(", ")}</span></div>
                        )}
                      </div>
                    )}
                  </div>
                ))
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
