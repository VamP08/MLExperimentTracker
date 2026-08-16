/// <reference types="vite/client" />

interface ImportMetaEnv {
  /**
   * `'1'` in the static demo build, absent in every other build.
   *
   * Read only through `lib/api.ts`. It is a build-time constant, so Vite replaces the
   * expression with a literal and the dead branch — along with the whole demo adapter and
   * the snapshot it imports — is dropped from the normal bundle.
   */
  readonly VITE_DEMO?: string;
}
