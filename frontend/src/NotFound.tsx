export function NotFound() {
  return <section className="not-found" aria-labelledby="not-found-heading">
    <p className="not-found-code" aria-hidden="true">404</p>
    <h1 id="not-found-heading">Page not found</h1>
    <p>The page you requested is unavailable. Search for a Hemi block or browse indexed publications.</p>
    <div className="not-found-actions"><a className="primary" href="/">Go to search</a><a className="secondary" href="/browse">Browse publications</a></div>
  </section>;
}
