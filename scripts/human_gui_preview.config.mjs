// Preview already-built files only: no source transforms, plugins or config bundling.
// The supervisor supplies a numeric loopback GUI port; reject another destination.
const backend = process.env.GAME_SERVER_URL || ''
const match = /^http:\/\/127\.0\.0\.1:(\d+)\/?$/.exec(backend)
if (!match || Number(match[1]) < 1 || Number(match[1]) > 65535) {
  throw new Error('GUI preview requires a plain HTTP loopback backend with an explicit port')
}
const target = new URL(backend)

export default {
  root: process.cwd(),
  preview: {
    proxy: {
      '/game': { target: target.origin, ws: true, changeOrigin: true },
      '/api': { target: target.origin, changeOrigin: true },
    },
  },
}
