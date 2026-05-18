/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_TBS_KIT_API_BASE?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
