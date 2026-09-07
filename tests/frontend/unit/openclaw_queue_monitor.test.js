import { describe, expect, it, vi } from "vitest";

vi.mock("../../../web/openclaw_api.js", () => ({
    openclawApi: {
        getHealth: vi.fn(),
        subscribeEvents: vi.fn(),
    },
}));

const { QueueMonitor } = await import("../../../web/openclaw_queue_monitor.js");

describe("QueueMonitor", () => {
    it("deduplicates repeated status banners within the ttl window", () => {
        const ui = { showBanner: vi.fn() };
        let nowValue = 1000;
        const monitor = new QueueMonitor(ui, {
            api: {},
            now: () => nowValue,
            setIntervalRef: vi.fn(),
        });

        monitor.showBanner("info", "Queued", "job_queued", 5000);
        nowValue = 2000;
        monitor.showBanner("info", "Queued", "job_queued", 5000);
        nowValue = 7000;
        monitor.showBanner("info", "Queued", "job_queued", 5000);

        expect(ui.showBanner).toHaveBeenCalledTimes(2);
        expect(ui.showBanner.mock.calls[0][0].severity).toBe("info");
    });

    it("reconnects the event stream when health checks recover from a disconnect", async () => {
        const ui = { showBanner: vi.fn() };
        const closedStream = { readyState: 2, close: vi.fn() };
        const subscribeEvents = vi.fn(() => ({ readyState: 1, close: vi.fn() }));
        const monitor = new QueueMonitor(ui, {
            api: {
                getHealth: vi.fn().mockResolvedValue({
                    ok: true,
                    data: { stats: { observability: { total_dropped: 0 } } },
                }),
                subscribeEvents,
            },
            setIntervalRef: vi.fn(),
        });

        monitor.isConnected = false;
        monitor.es = closedStream;

        await monitor.checkHealth();

        expect(subscribeEvents).toHaveBeenCalledTimes(1);
        expect(ui.showBanner).toHaveBeenCalledWith(
            expect.objectContaining({
                id: "connection_restored",
                severity: "success",
            })
        );
    });

    it("does not alert immediately for the first startup disconnect", () => {
        const ui = { showBanner: vi.fn() };
        const monitor = new QueueMonitor(ui, {
            api: {
                subscribeEvents: vi.fn(() => ({ readyState: 1, close: vi.fn() })),
            },
            now: () => 1000,
            setIntervalRef: vi.fn(),
            startupGraceMs: 30000,
            disconnectAlertThreshold: 3,
        });

        monitor.start();
        monitor.handleConnectionError(new Error("offline"));

        expect(ui.showBanner).not.toHaveBeenCalled();
        expect(monitor.isConnected).toBe(false);
    });

    it("alerts after sustained startup disconnect failures cross the grace threshold", () => {
        const ui = { showBanner: vi.fn() };
        let nowValue = 0;
        const monitor = new QueueMonitor(ui, {
            api: {
                subscribeEvents: vi.fn(() => ({ readyState: 1, close: vi.fn() })),
            },
            now: () => nowValue,
            setIntervalRef: vi.fn(),
            startupGraceMs: 1000,
            disconnectAlertThreshold: 3,
        });

        monitor.start();
        monitor.handleConnectionError(new Error("offline"));
        nowValue = 500;
        monitor.handleConnectionError(new Error("offline"));
        nowValue = 1500;
        monitor.handleConnectionError(new Error("offline"));

        expect(ui.showBanner).toHaveBeenCalledWith(
            expect.objectContaining({
                id: "connection_lost",
                severity: "error",
                persist: true,
            })
        );
    });

    it("alerts immediately once a previously healthy backend disconnects", async () => {
        const ui = { showBanner: vi.fn() };
        const monitor = new QueueMonitor(ui, {
            api: {
                getHealth: vi.fn().mockResolvedValue({
                    ok: true,
                    data: { stats: { observability: { total_dropped: 0 } } },
                }),
                subscribeEvents: vi.fn(),
            },
            now: () => 1000,
            setIntervalRef: vi.fn(),
            startupGraceMs: 30000,
            disconnectAlertThreshold: 3,
        });

        await monitor.checkHealth();
        ui.showBanner.mockClear();

        monitor.handleConnectionError(new Error("offline"));

        // The operator is still told on the very first failure. What changed is that the
        // immediate banner leaves no record: a permanent error outlives the blip that raised
        // it, and a restart is the common case. The durable error is asserted below, once the
        // outage has actually been confirmed.
        expect(ui.showBanner).toHaveBeenCalledWith(
            expect.objectContaining({
                id: "connection_lost_pending",
                severity: "warning",
                persist: false,
            })
        );
    });

    it("persists the durable error only once the disconnect is confirmed", async () => {
        const ui = { showBanner: vi.fn() };
        const monitor = new QueueMonitor(ui, {
            api: {
                getHealth: vi.fn().mockResolvedValue({
                    ok: true,
                    data: { stats: { observability: { total_dropped: 0 } } },
                }),
                subscribeEvents: vi.fn(),
            },
            now: () => 1000,
            setIntervalRef: vi.fn(),
            startupGraceMs: 30000,
            disconnectAlertThreshold: 3,
        });

        await monitor.checkHealth();
        ui.showBanner.mockClear();

        monitor.handleConnectionError(new Error("offline"));
        monitor.handleConnectionError(new Error("offline"));
        monitor.handleConnectionError(new Error("offline"));

        expect(ui.showBanner).toHaveBeenCalledWith(
            expect.objectContaining({
                id: "connection_lost",
                severity: "error",
                persist: true,
            })
        );
    });

    it("emits persistent failed-job notifications with a job-monitor jump action", () => {
        const ui = { showBanner: vi.fn() };
        const monitor = new QueueMonitor(ui, {
            api: {},
            now: () => 1000,
            setIntervalRef: vi.fn(),
        });

        monitor.handleEvent({
            event_type: "failed",
            prompt_id: "prompt-12345678",
        });

        expect(ui.showBanner).toHaveBeenCalledWith(
            expect.objectContaining({
                severity: "error",
                persist: true,
                action: expect.objectContaining({
                    type: "tab",
                    payload: "job-monitor",
                }),
            })
        );
    });

    it("clears stale active prompt ids after reconnect when the queue snapshot no longer lists them", async () => {
        const ui = { showBanner: vi.fn() };
        const closedStream = { readyState: 2, close: vi.fn() };
        const monitor = new QueueMonitor(ui, {
            api: {
                getHealth: vi.fn().mockResolvedValue({
                    ok: true,
                    data: { stats: { observability: { total_dropped: 0 } } },
                }),
                getPromptQueue: vi.fn().mockResolvedValue({
                    ok: true,
                    data: {
                        queue_running: [],
                        queue_pending: [[1, "still-active"]],
                    },
                }),
                subscribeEvents: vi.fn(() => ({ readyState: 1, close: vi.fn() })),
            },
            setIntervalRef: vi.fn(),
        });

        monitor.handleEvent({ event_type: "running", prompt_id: "stale-job-1" });
        monitor.handleEvent({ event_type: "queued", prompt_id: "still-active" });
        monitor.isConnected = false;
        monitor.es = closedStream;

        await monitor.checkHealth();

        expect(monitor.activePromptIds.has("stale-job-1")).toBe(false);
        expect(monitor.activePromptIds.has("still-active")).toBe(true);
        expect(ui.showBanner).toHaveBeenCalledWith(
            expect.objectContaining({
                id: "job_reconnect_cleared_stale-jo",
                severity: "info",
            })
        );
    });

    it("preserves active prompt ids when reconnect queue refresh fails", async () => {
        const ui = { showBanner: vi.fn() };
        const monitor = new QueueMonitor(ui, {
            api: {
                getHealth: vi.fn().mockResolvedValue({
                    ok: true,
                    data: { stats: { observability: { total_dropped: 0 } } },
                }),
                getPromptQueue: vi.fn().mockResolvedValue({
                    ok: false,
                    error: "queue_unavailable",
                }),
                subscribeEvents: vi.fn(() => ({ readyState: 1, close: vi.fn() })),
            },
            setIntervalRef: vi.fn(),
        });

        monitor.handleEvent({ event_type: "running", prompt_id: "active-job" });
        monitor.isConnected = false;

        await monitor.checkHealth();

        expect(monitor.activePromptIds.has("active-job")).toBe(true);
        expect(ui.showBanner).not.toHaveBeenCalledWith(
            expect.objectContaining({
                id: expect.stringMatching(/^job_reconnect_cleared_/),
            })
        );
    });
    // Connectivity alert lifecycle. A blip must be visible but must not leave a
    // permanent record; a confirmed outage must leave exactly one; and a recovery must
    // retire it so the dismissed-entry tombstone cannot mute the next outage.
    function makeStore() {
        const entries = new Map();
        return {
            entries,
            notify: vi.fn((payload) => {
                const key = payload.dedupeKey || payload.dedupe_key;
                entries.set(key, { ...payload, dedupe_key: key });
                return entries.get(key);
            }),
            resolveByDedupeKey: vi.fn((key) => {
                const found = entries.get(key) || null;
                entries.delete(key);
                return found;
            }),
        };
    }

    // The monitor talks to the store through the banner manager, so the harness mirrors what
    // web/openclaw_banner_manager.js:101-113 does: persist warning/error unless the payload
    // says otherwise, under the stable `banner:<id>` dedupe key.
    function makeUi(store) {
        return {
            showBanner: vi.fn((payload) => {
                const severity = payload.severity || "info";
                const shouldPersist = payload.persist != null
                    ? Boolean(payload.persist)
                    : severity === "warning" || severity === "error";
                if (shouldPersist) {
                    store.notify({
                        severity,
                        message: payload.message,
                        dedupeKey: `banner:${payload.id}`,
                    });
                }
            }),
        };
    }

    function healthyResponse() {
        return { ok: true, data: { stats: { observability: { total_dropped: 0 } } } };
    }

    function makeMonitor(store, getHealth) {
        const ui = makeUi(store);
        const monitor = new QueueMonitor(ui, {
            api: {
                getHealth,
                getPromptQueue: vi.fn().mockResolvedValue({ ok: true, data: {} }),
                subscribeEvents: vi.fn(() => ({ readyState: 1, close: vi.fn() })),
            },
            notifications: store,
            now: () => 1000,
            setIntervalRef: vi.fn(),
            startupGraceMs: 30000,
            disconnectAlertThreshold: 3,
        });
        return { monitor, ui };
    }

    it("does not persist a record for a single blip after the backend was healthy", async () => {
        const store = makeStore();
        const getHealth = vi.fn()
            .mockResolvedValueOnce(healthyResponse())
            .mockResolvedValueOnce({ ok: false });
        const { monitor } = makeMonitor(store, getHealth);

        await monitor.checkHealth();
        await monitor.checkHealth();

        expect(store.entries.size).toBe(0);
    });

    it("still warns immediately on that first blip, without persisting it", async () => {
        const store = makeStore();
        const getHealth = vi.fn()
            .mockResolvedValueOnce(healthyResponse())
            .mockResolvedValueOnce({ ok: false });
        const { monitor, ui } = makeMonitor(store, getHealth);

        await monitor.checkHealth();
        ui.showBanner.mockClear();
        await monitor.checkHealth();

        expect(ui.showBanner).toHaveBeenCalledTimes(1);
        expect(ui.showBanner).toHaveBeenCalledWith(
            expect.objectContaining({ severity: "warning", persist: false })
        );
    });

    it("emits the transient blip warning only once per outage", async () => {
        const store = makeStore();
        const getHealth = vi.fn()
            .mockResolvedValueOnce(healthyResponse())
            .mockResolvedValue({ ok: false });
        const { monitor, ui } = makeMonitor(store, getHealth);

        await monitor.checkHealth();
        ui.showBanner.mockClear();
        await monitor.checkHealth();
        await monitor.checkHealth();

        const transient = ui.showBanner.mock.calls.filter(
            ([payload]) => payload.persist === false
        );
        expect(transient).toHaveLength(1);
    });

    it("persists exactly one error once the outage is confirmed", async () => {
        const store = makeStore();
        const getHealth = vi.fn()
            .mockResolvedValueOnce(healthyResponse())
            .mockResolvedValue({ ok: false });
        const { monitor } = makeMonitor(store, getHealth);

        await monitor.checkHealth();
        await monitor.checkHealth();
        await monitor.checkHealth();
        await monitor.checkHealth();
        await monitor.checkHealth();

        expect(store.entries.size).toBe(1);
        expect(store.entries.get("banner:health_check_failed").severity).toBe("error");
    });

    it("retires the persisted connectivity error when the backend recovers", async () => {
        const store = makeStore();
        const getHealth = vi.fn()
            .mockResolvedValueOnce(healthyResponse())
            .mockResolvedValueOnce({ ok: false })
            .mockResolvedValueOnce({ ok: false })
            .mockResolvedValueOnce({ ok: false })
            .mockResolvedValue(healthyResponse());
        const { monitor } = makeMonitor(store, getHealth);

        await monitor.checkHealth();
        await monitor.checkHealth();
        await monitor.checkHealth();
        await monitor.checkHealth();
        expect(store.entries.has("banner:health_check_failed")).toBe(true);

        await monitor.checkHealth();

        expect(store.entries.has("banner:health_check_failed")).toBe(false);
    });

    it("does not mute the next outage after a recovery retired the previous one", async () => {
        const store = makeStore();
        const getHealth = vi.fn()
            .mockResolvedValueOnce(healthyResponse())
            .mockResolvedValueOnce({ ok: false })
            .mockResolvedValueOnce({ ok: false })
            .mockResolvedValueOnce({ ok: false })
            .mockResolvedValueOnce(healthyResponse())
            .mockResolvedValue({ ok: false });
        const { monitor } = makeMonitor(store, getHealth);

        for (let i = 0; i < 5; i += 1) {
            await monitor.checkHealth();
        }
        expect(store.entries.has("banner:health_check_failed")).toBe(false);

        await monitor.checkHealth();
        await monitor.checkHealth();
        await monitor.checkHealth();

        expect(store.entries.has("banner:health_check_failed")).toBe(true);
    });

    it("retires the backpressure warning once the host reports no dropped events", async () => {
        const store = makeStore();
        const dropping = {
            ok: true,
            data: { stats: { observability: { total_dropped: 4 } } },
        };
        const getHealth = vi.fn()
            .mockResolvedValueOnce(dropping)
            .mockResolvedValue(healthyResponse());
        const { monitor } = makeMonitor(store, getHealth);

        await monitor.checkHealth();
        expect(store.entries.has("banner:backpressure")).toBe(true);

        await monitor.checkHealth();

        expect(store.entries.has("banner:backpressure")).toBe(false);
    });

    it("stays entirely quiet during the bootstrap race, however many polls fail", async () => {
        const store = makeStore();
        const getHealth = vi.fn().mockResolvedValue({ ok: false });
        const { monitor, ui } = makeMonitor(store, getHealth);

        await monitor.checkHealth();
        await monitor.checkHealth();
        await monitor.checkHealth();
        await monitor.checkHealth();

        expect(store.entries.size).toBe(0);
        expect(ui.showBanner).not.toHaveBeenCalled();
    });

    it("does not accumulate isolated blips toward the confirmation threshold", async () => {
        const store = makeStore();
        const getHealth = vi.fn()
            .mockResolvedValueOnce(healthyResponse())
            .mockResolvedValueOnce({ ok: false })
            .mockResolvedValueOnce(healthyResponse())
            .mockResolvedValueOnce({ ok: false })
            .mockResolvedValueOnce(healthyResponse())
            .mockResolvedValueOnce({ ok: false });
        const { monitor } = makeMonitor(store, getHealth);

        for (let i = 0; i < 6; i += 1) {
            await monitor.checkHealth();
        }

        expect(store.entries.size).toBe(0);
    });

    it("leaves notifications the queue monitor did not raise alone on recovery", async () => {
        const store = makeStore();
        const getHealth = vi.fn().mockResolvedValue(healthyResponse());
        const { monitor } = makeMonitor(store, getHealth);

        store.notify({
            severity: "error",
            message: "❌ Job abc12345 failed",
            dedupeKey: "banner:job_failed_abc12345",
        });

        await monitor.checkHealth();

        expect(store.entries.has("banner:job_failed_abc12345")).toBe(true);
    });
});
