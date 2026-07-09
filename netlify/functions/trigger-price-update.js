const defaultHeaders = {
  "Access-Control-Allow-Headers": "content-type",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
  "Content-Type": "application/json; charset=utf-8"
};

function response(statusCode, body, origin) {
  return {
    statusCode,
    headers: {
      ...defaultHeaders,
      "Access-Control-Allow-Origin": origin || "*"
    },
    body: JSON.stringify(body)
  };
}

exports.handler = async (event) => {
  const allowedOrigin = process.env.ALLOWED_ORIGIN || "*";
  const origin = allowedOrigin === "*" ? "*" : allowedOrigin;

  if (event.httpMethod === "OPTIONS") {
    return response(204, {}, origin);
  }

  if (event.httpMethod !== "POST") {
    return response(405, { ok: false, error: "Method not allowed" }, origin);
  }

  const token = process.env.GITHUB_TOKEN;
  const owner = process.env.GITHUB_OWNER;
  const repo = process.env.GITHUB_REPO;
  const workflow = process.env.GITHUB_WORKFLOW || "update-market-data.yml";
  const ref = process.env.GITHUB_REF || "main";

  if (!token || !owner || !repo) {
    return response(500, {
      ok: false,
      error: "Missing GITHUB_TOKEN, GITHUB_OWNER, or GITHUB_REPO environment variable"
    }, origin);
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
    body: JSON.stringify({ ref })
  });

  if (!githubResponse.ok) {
    const text = await githubResponse.text();
    return response(githubResponse.status, {
      ok: false,
      error: "GitHub workflow dispatch failed",
      detail: text
    }, origin);
  }

  return response(202, {
    ok: true,
    mode: "github-actions",
    workflow,
    ref
  }, origin);
};
