import { beforeEach, describe, expect, it } from "vitest";

import { OpenClawNotifications } from "../../../web/openclaw_notifications.js";

describe("OpenClawNotifications", () => {
    beforeEach(() => {
        localStorage.clear();
    });

    it("deduplicates active entries by dedupe key and increments the count", () => {
        let nowValue = Date.parse("2026-03-19T00:00:00Z");
        const store = new OpenClawNotifications({
            storage: localStorage,
            now: () => nowValue,
        });

        store.notify({
            severity: "error",
            source: "model-manager",
            message: "search failed: search_failed",
            dedupeKey: "model-manager:search",
        });

        nowValue += 1_000;
        store.notify({
            severity: "error",
            source: "model-manager",
            message: "search failed: search_failed",
            dedupeKey: "model-manager:search",
        });

        const entries = store.getEntries();
        expect(entries).toHaveLength(1);
        expect(entries[0].count).toBe(2);
        expect(entries[0].acknowledged_at).toBeNull();
    });

    it("persists acknowledgement and dismissal state in local storage", () => {
        const store = new OpenClawNotifications({
            storage: localStorage,
            now: () => Date.parse("2026-03-19T00:00:00Z"),
        });

        const entry = store.notify({
            severity: "warning",
            source: "queue-monitor",
            message: "High load: dropped events",
            dedupeKey: "queue-monitor:backpressure",
        });

        store.acknowledge(entry.id);
        store.dismiss(entry.id);

        const reloaded = new OpenClawNotifications({
            storage: localStorage,
            now: () => Date.parse("2026-03-19T00:00:01Z"),
        });

        expect(reloaded.getEntries()).toHaveLength(0);
        expect(reloaded.getEntries({ includeDismissed: true })[0].dismissed_at).not.toBeNull();
        expect(reloaded.getEntries({ includeDismissed: true })[0].acknowledged_at).not.toBeNull();
    });

    it("does not resurrect an identical dismissed notification on repeated auto-refresh", () => {
        let nowValue = Date.parse("2026-03-19T00:00:00Z");
        const store = new OpenClawNotifications({
            storage: localStorage,
            now: () => nowValue,
        });

        const entry = store.notify({
            severity: "error",
            source: "model-manager",
            message: "search: search_failed",
            dedupeKey: "model-manager:refresh",
        });

        store.dismiss(entry.id);
        expect(store.getEntries()).toHaveLength(0);

        nowValue += 1_000;
        store.notify({
            severity: "error",
            source: "model-manager",
            message: "search: search_failed",
            dedupeKey: "model-manager:refresh",
        });

        expect(store.getEntries()).toHaveLength(0);
        const dismissed = store.getEntries({ includeDismissed: true });
        expect(dismissed).toHaveLength(1);
        expect(dismissed[0].dismissed_at).not.toBeNull();
    });

    // A condition that has ended must leave no tombstone. The dismissed-entry suppression
    // above is deliberate and protects an operator who dismisses an error while the
    // condition is still ongoing; only the producer knows a condition ended, so the store
    // exposes resolution and the producer calls it.
    const connectivityPayload = {
        id: "banner_health_check_failed",
        severity: "error",
        source: "queue-monitor",
        message: "⚠️ Backend Unreachable",
        dedupeKey: "banner:health_check_failed",
    };

    it("resolves an active entry by dedupe key and returns it", () => {
        const store = new OpenClawNotifications({ storage: localStorage });

        store.notify(connectivityPayload);
        expect(store.getEntries()).toHaveLength(1);

        const resolved = store.resolveByDedupeKey("banner:health_check_failed");

        expect(resolved).not.toBeNull();
        expect(resolved.message).toBe("⚠️ Backend Unreachable");
        expect(store.getEntries({ includeDismissed: true })).toHaveLength(0);
    });

    it("resolves a dismissed tombstone so nothing remains under that key", () => {
        const store = new OpenClawNotifications({ storage: localStorage });

        const entry = store.notify(connectivityPayload);
        store.dismiss(entry.id);
        expect(store.getEntries({ includeDismissed: true })).toHaveLength(1);

        store.resolveByDedupeKey("banner:health_check_failed");

        expect(store.getEntries({ includeDismissed: true })).toHaveLength(0);
    });

    it("lets a resolved condition recur after the operator dismissed it", () => {
        const store = new OpenClawNotifications({ storage: localStorage });

        const entry = store.notify(connectivityPayload);
        store.dismiss(entry.id);
        store.resolveByDedupeKey("banner:health_check_failed");

        store.notify(connectivityPayload);

        const active = store.getEntries();
        expect(active).toHaveLength(1);
        expect(active[0].dismissed_at).toBeNull();
        expect(active[0].acknowledged_at).toBeNull();
        expect(active[0].count).toBe(1);
        expect(store.getUnreadCount()).toBe(1);
    });

    it("resolves only the requested key and leaves other entries untouched", () => {
        const store = new OpenClawNotifications({ storage: localStorage });

        store.notify(connectivityPayload);
        store.notify({
            id: "banner_backpressure",
            severity: "warning",
            source: "queue-monitor",
            message: "⚠️ High load: 4 events dropped.",
            dedupeKey: "banner:backpressure",
        });
        store.notify({
            id: "banner_job_failed_abc",
            severity: "error",
            source: "queue-monitor",
            message: "❌ Job abc failed",
            dedupeKey: "banner:job_failed_abc",
        });

        store.resolveByDedupeKey("banner:health_check_failed");

        const remaining = store.getEntries({ includeDismissed: true });
        expect(remaining).toHaveLength(2);
        expect(remaining.map((item) => item.dedupe_key).sort()).toEqual([
            "banner:backpressure",
            "banner:job_failed_abc",
        ]);
    });

    it("treats unknown, empty, and undefined keys as no-ops", () => {
        const store = new OpenClawNotifications({ storage: localStorage });

        store.notify(connectivityPayload);

        expect(store.resolveByDedupeKey("banner:never_raised")).toBeNull();
        expect(store.resolveByDedupeKey("")).toBeNull();
        expect(store.resolveByDedupeKey(undefined)).toBeNull();
        expect(store.getEntries({ includeDismissed: true })).toHaveLength(1);
    });

    it("keeps dedupe keys unique across a dismiss, notify, and resolve cycle", () => {
        const store = new OpenClawNotifications({ storage: localStorage });

        const entry = store.notify(connectivityPayload);
        store.dismiss(entry.id);
        store.notify(connectivityPayload);
        store.resolveByDedupeKey("banner:health_check_failed");
        store.notify(connectivityPayload);

        const all = store.getEntries({ includeDismissed: true });
        const keys = all.map((item) => item.dedupe_key);
        expect(new Set(keys).size).toBe(keys.length);
    });
});
