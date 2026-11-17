// Sends push notifications for a speeding event to the guards already chosen by
// the database trigger (dispatch_speeding_event).
//
// Called by pg_net from that trigger with {"event_id": n}. JWT verification is
// off because the caller is the database, not a user. That is safe because the
// body only names an event: claim_push_batch() marks rows as pushed in the same
// statement, so a repeated or spoofed call can only send pushes that were
// already due, never new ones.
//
// Needs one secret, set in the dashboard (Edge Functions -> Secrets):
//   FCM_SERVICE_ACCOUNT = the Firebase service-account JSON
// Without it nothing is pushed, but guards with the app open still get the
// alert instantly over Realtime.

import { createClient } from "npm:@supabase/supabase-js@2";

const db = createClient(
  Deno.env.get("SUPABASE_URL")!,
  Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!,
);
const COMPASS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"];

type ServiceAccount = { project_id: string; client_email: string; private_key: string };

const b64url = (bytes: Uint8Array) =>
  btoa(String.fromCharCode(...bytes)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");

// Google OAuth access token from the service account, cached while the
// function instance stays warm (tokens last an hour).
let cached: { token: string; expires: number } | null = null;

async function accessToken(sa: ServiceAccount): Promise<string> {
  if (cached && cached.expires > Date.now() + 60_000) return cached.token;
  const now = Math.floor(Date.now() / 1000);
  const json = (o: object) => b64url(new TextEncoder().encode(JSON.stringify(o)));
  const unsigned = `${json({ alg: "RS256", typ: "JWT" })}.${json({
    iss: sa.client_email,
    scope: "https://www.googleapis.com/auth/firebase.messaging",
    aud: "https://oauth2.googleapis.com/token",
    iat: now,
    exp: now + 3600,
  })}`;
  const der = Uint8Array.from(
    atob(sa.private_key.replace(/-----[^-]+-----/g, "").replace(/\s/g, "")),
    (c) => c.charCodeAt(0),
  );
  const key = await crypto.subtle.importKey(
    "pkcs8", der, { name: "RSASSA-PKCS1-v1_5", hash: "SHA-256" }, false, ["sign"],
  );
  const sig = new Uint8Array(
    await crypto.subtle.sign("RSASSA-PKCS1-v1_5", key, new TextEncoder().encode(unsigned)),
  );
  const res = await fetch("https://oauth2.googleapis.com/token", {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      grant_type: "urn:ietf:params:oauth:grant-type:jwt-bearer",
      assertion: `${unsigned}.${b64url(sig)}`,
    }),
  });
  const body = await res.json();
  if (!res.ok) throw new Error(`oauth: ${JSON.stringify(body)}`);
  cached = { token: body.access_token, expires: Date.now() + body.expires_in * 1000 };
  return cached.token;
}

const distanceText = (m: number) => {
  const r = Math.round(m / 10) * 10; // round first, so 999 m reads "1.0 km", not "1000 m"
  return r < 1000 ? `${r} m` : `${(m / 1000).toFixed(1)} km`;
};

// {"check": true}: confirms the secret is one valid key and can sign in to
// Google. Reports the Firebase project id, never the key itself.
async function check(saJson: string | undefined) {
  if (!saJson) return { secret: "missing" };
  let sa: ServiceAccount;
  try {
    sa = JSON.parse(saJson);
  } catch {
    return { secret: "not valid JSON (two keys pasted together?)" };
  }
  try {
    await accessToken(sa);
    return { secret: "ok", project_id: sa.project_id, fcm_auth: "ok" };
  } catch (e) {
    return { secret: "parsed", project_id: sa.project_id, fcm_auth: `failed: ${String(e).slice(0, 160)}` };
  }
}

Deno.serve(async (req) => {
  const body = await req.json().catch(() => ({}));
  const saJson = Deno.env.get("FCM_SERVICE_ACCOUNT");
  if (body.check === true) return Response.json(await check(saJson));

  const { event_id } = body;
  if (!Number.isInteger(event_id)) return Response.json({ error: "event_id required" }, { status: 400 });

  if (!saJson) return Response.json({ sent: 0, reason: "FCM_SERVICE_ACCOUNT not set" });
  const sa: ServiceAccount = JSON.parse(saJson);

  const { data: rows, error } = await db.rpc("claim_push_batch", { p_event_id: event_id });
  if (error) return Response.json({ error: error.message }, { status: 500 });
  if (!rows?.length) return Response.json({ sent: 0 });

  const token = await accessToken(sa);
  const url = `https://fcm.googleapis.com/v1/projects/${sa.project_id}/messages:send`;

  const results = await Promise.all(rows.map(async (r: Record<string, any>) => {
    const dir = COMPASS[Math.round(r.bearing_deg / 45) % 8];
    const speed = Math.round(r.speed_kmh);
    const message = {
      token: r.fcm_token,
      // Readable from the lock screen without opening the app.
      notification: {
        title: `Overspeed · ${speed} km/h`,
        body: `${r.vehicle_type} · limit ${Math.round(r.speed_limit)} · ${distanceText(r.distance_m)} ${dir} · ${r.camera}`,
      },
      // Everything the alert screen needs, so it renders without a fetch.
      data: Object.fromEntries(Object.entries({
        event_id, dispatch_id: r.dispatch_id, speed_kmh: r.speed_kmh, speed_limit: r.speed_limit,
        vehicle_type: r.vehicle_type, camera: r.camera, lat: r.lat, lon: r.lon,
        distance_m: r.distance_m, bearing_deg: r.bearing_deg, detected_at: r.detected_at,
      }).map(([k, v]) => [k, String(v)])),
      android: {
        priority: "HIGH",
        notification: { channel_id: "overspeed", sound: "default", tag: `event-${event_id}` },
      },
      apns: {
        headers: { "apns-priority": "10", "apns-push-type": "alert" },
        payload: { aps: { sound: "default", "interruption-level": "time-sensitive" } },
      },
    };
    const res = await fetch(url, {
      method: "POST",
      headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
      body: JSON.stringify({ message }),
    });
    if (res.ok) return true;
    const err = await res.text();
    if (res.status === 404 || err.includes("UNREGISTERED")) {
      // App uninstalled or token rotated: stop sending to it.
      await db.from("guards").update({ fcm_token: null }).eq("fcm_token", r.fcm_token);
    }
    console.error(`fcm ${res.status} for dispatch ${r.dispatch_id}: ${err}`);
    return false;
  }));

  const sent = results.filter(Boolean).length;
  return Response.json({ sent, failed: results.length - sent });
});
