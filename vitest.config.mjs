import { defineConfig } from "vitest/config";

export default defineConfig({
    test: {
        environment: "jsdom",
        setupFiles: ["./web/tests/unit/setup.js"],
        include: ["web/tests/unit/**/*.test.js"],
        restoreMocks: true,
        clearMocks: true,
    },
});
