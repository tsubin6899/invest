function send(res, statusCode, body) {
  res.setHeader("Content-Type", "application/json; charset=utf-8");
  res.setHeader("Access-Control-Allow-Headers", "content-type");
  res.setHeader("Access-Control-Allow-Methods", "POST, OPTIONS");
  res.status(statusCode).json(body);
}

export default async function handler(req, res) {
  if (req.method === "OPTIONS") {
    send(res, 204, {});
    return;
  }

  if (req.method !== "POST") {
    send(res, 405, { ok: false, error: "Method not allowed" });
    return;
  }

  const token = process.env.GITHUB_TOKEN;
  const owner = process.env.GITHUB_OWNER;
  const repo = process.env.GITHUB_REPO;
  const workflow = process.env.GITHUB_WORKFLOW || "update-market-data.yml";
  const ref = process.env.GITHUB_REF || "main";
  let symbolsJson = "";
  if (req.body) {
    const symbols = typeof req.body === "string" ? JSON.parse(req.body) : req.body;
    if (!Array.isArray(symbols.tw) || !Array.isArray(symbols.us)) {
      send(res, 400, { ok: false, error: "Invalid symbols payload" });
      return;
    }
    symbolsJson = JSON.stringify(symbols);
  }

  if (!token || !owner || !repo) {
    send(res, 500, {
      ok: false,
      error: "Missing GITHUB_TOKEN, GITHUB_OWNER, or GITHUB_REPO environment variable"
    });
    return;
  }

  const dispatchUrl = `https://api.github.com/repos/${owner}/${repo}/actions/workflows/${encodeURIComponent(workflow)}/dispatches`;
  const githubResponse = await fetch(dispatchUrl, {
    method: "POST",
    headers: {
      "Accept": "application/vnd.github+json",
      "Authorization": `Bearer ${token}`,
      "Content-Type": "application/json",
      "User-Agent": "personal-assets-dashboard"
    },
    body: JSON.stringify({ ref, inputs: symbolsJson ? { symbols_json: symbolsJson } : {} })
  });

  if (!githubResponse.ok) {
    const detail = await githubResponse.text();
    send(res, githubResponse.status, {
      ok: false,
      error: "GitHub workflow dispatch failed",
      detail
    });
    return;
  }

  send(res, 202, {
    ok: true,
    mode: "github-actions",
    workflow,
    ref
  });
}
