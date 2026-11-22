const proxy = process.env.HTTPS_PROXY || process.env.https_proxy;
if (proxy) {
  const { setGlobalDispatcher, ProxyAgent } = require('/home/box/.proxyshim/node_modules/undici');
  setGlobalDispatcher(
    new ProxyAgent({
      uri: proxy,
      headersTimeout: 90_000,
      bodyTimeout: 90_000,
    })
  );
}
