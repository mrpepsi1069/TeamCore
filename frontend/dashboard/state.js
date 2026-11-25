// Shared, in-memory UI state.
export const LEVELS = { viewer: 1, staff: 2, manager: 3, admin: 4 };
export const state = {
  user: null, prefs: { accent: "#FF7A1A", timezone: "UTC" },
  guilds: [], gid: null, level: null, gmeta: null, specs: {}, settingsSpec: [],
};
export const can = (need) => (LEVELS[state.level] || 0) >= LEVELS[need];
