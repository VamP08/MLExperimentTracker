/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** '1' in the static demo build, unset otherwise. Only read through lib/api.ts. */
  readonly VITE_DEMO?: string;
}
