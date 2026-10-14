import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
import { existsSync, realpathSync } from 'node:fs';
import { relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

// The output folder now contains generated frontend files only. Verify the
// canonical target before Vite clears it, including during watch rebuilds.
const workspaceRoot = realpathSync(fileURLToPath(new URL('../', import.meta.url)));
const outputDirectory = resolve(workspaceRoot, 'web');
if (existsSync(outputDirectory) && relative(workspaceRoot, realpathSync(outputDirectory)) !== 'web') {
  throw new Error('Frontend output must resolve to the workspace web directory');
}

export default defineConfig({
  plugins: [react()],
  build: {
    outDir: outputDirectory,
    emptyOutDir: true,
    sourcemap: false,
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test-setup.ts'],
    include: ['src/**/*.test.ts', 'src/**/*.test.tsx'],
    clearMocks: true,
  },
});
