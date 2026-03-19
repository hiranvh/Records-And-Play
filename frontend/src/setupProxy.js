const { createProxyMiddleware } = require('http-proxy-middleware');

module.exports = function(app) {
  app.use(
    '/record',
    createProxyMiddleware({
      target: 'http://localhost:8004',
      changeOrigin: true,
    })
  );
  app.use(
    '/flows',
    createProxyMiddleware({
      target: 'http://localhost:8004',
      changeOrigin: true,
    })
  );
  app.use(
    '/ai',
    createProxyMiddleware({
      target: 'http://localhost:8004',
      changeOrigin: true,
    })
  );
  app.use(
    '/config',
    createProxyMiddleware({
      target: 'http://localhost:8004',
      changeOrigin: true,
    })
  );
};