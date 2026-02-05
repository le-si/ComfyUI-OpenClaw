export function createMockComfyUIApp() {
  const settingsStore = new Map();

  const app = {
    ui: {
      settings: {
        addSetting: ({ id, defaultValue }) => {
          if (!settingsStore.has(id)) settingsStore.set(id, defaultValue);
        },
        getSettingValue: (id, fallback) => {
          return settingsStore.has(id) ? settingsStore.get(id) : fallback;
        },
      },
    },

    extensionManager: {
      registerSidebarTab: ({ id, title, render }) => {
        const root = document.getElementById('mock-sidebar-tabs');
        const host = document.createElement('div');
        host.id = `sidebar-tab-${id}`;
        host.style.height = '100vh';
        root.replaceChildren(host);
        render(host);
      },
    },

    registerExtension: ({ name, setup }) => {
      // Simulate ComfyUI calling setup immediately.
      return Promise.resolve(setup());
    },
  };

  return app;
}
