/**
 * R164: Explicit frontend host-surface detection helpers.
 * Keep desktop-vs-standalone assumptions centralized so extension code does not
 * silently treat the desktop bundle as identical to standalone frontend HEAD.
 */

export const HOST_SURFACES = Object.freeze({
    standaloneFrontend: "standalone_frontend",
    desktop: "desktop",
});

export const HOST_SURFACE_REFERENCES = Object.freeze({
    [HOST_SURFACES.standaloneFrontend]: Object.freeze({
        frontendVersion: "1.46.13",
    }),
    [HOST_SURFACES.desktop]: Object.freeze({
        desktopVersion: "0.9.4",
        coreVersion: "0.22.3",
        embeddedFrontendVersion: "1.43.18",
        standaloneFrontendVersion: "1.46.13",
        frontendParity: "lagging",
    }),
});

function normalizeSurfaceName(surface) {
    if (surface === HOST_SURFACES.desktop || surface === "desktop") {
        return HOST_SURFACES.desktop;
    }
    if (
        surface === HOST_SURFACES.standaloneFrontend ||
        surface === "standalone" ||
        surface === "standalone_frontend" ||
        surface === "localhost"
    ) {
        return HOST_SURFACES.standaloneFrontend;
    }
    return null;
}

export function resolveHostSurface({ app = null, win = window } = {}) {
    const explicitSurface = normalizeSurfaceName(
        app?.openclawHostSurface || app?.hostSurface || win?.__OPENCLAW_HOST_SURFACE__
    );
    if (explicitSurface) return explicitSurface;

    const distributionSurface = normalizeSurfaceName(win?.__DISTRIBUTION__);
    if (distributionSurface) return distributionSurface;

    if (win?.electronAPI) {
        return HOST_SURFACES.desktop;
    }

    return HOST_SURFACES.standaloneFrontend;
}

export function getHostSurfaceCapabilities(options = {}) {
    const hostSurface = resolveHostSurface(options);
    const reference = HOST_SURFACE_REFERENCES[hostSurface] || {};
    return {
        hostSurface,
        isDesktop: hostSurface === HOST_SURFACES.desktop,
        supportsElectronBridge:
            hostSurface === HOST_SURFACES.desktop && !!options?.win?.electronAPI,
        reference,
    };
}

export function stampHostSurfaceMetadata(container, options = {}) {
    const capabilities = getHostSurfaceCapabilities(options);
    if (container?.dataset) {
        container.dataset.openclawHostSurface = capabilities.hostSurface;
        container.dataset.openclawDesktopHost = capabilities.isDesktop
            ? "true"
            : "false";
        container.dataset.openclawReferenceFrontend = capabilities.isDesktop
            ? capabilities.reference.standaloneFrontendVersion || ""
            : capabilities.reference.frontendVersion || "";
        if (capabilities.isDesktop) {
            container.dataset.openclawDesktopVersion = capabilities.reference.desktopVersion || "";
            container.dataset.openclawDesktopCoreVersion = capabilities.reference.coreVersion || "";
            container.dataset.openclawDesktopEmbeddedFrontend =
                capabilities.reference.embeddedFrontendVersion || "";
            container.dataset.openclawDesktopFrontendParity =
                capabilities.reference.frontendParity || "";
        }
    }
    return capabilities;
}
