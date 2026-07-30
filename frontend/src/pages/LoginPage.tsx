import './LoginPage.css';

/**
 * Nocturne's sign-in is left-aligned and asymmetric: a short accent rule, the
 * wordmark, one line of purpose copy, then a narrow column of actions with the
 * whitespace left on the right.
 *
 * The handoff's mock shows email/password fields and an Entra ID button. This
 * app authenticates with Google SSO only, so the column carries the single
 * real action rather than controls that don't exist.
 */
function LoginPage() {
  return (
    <div className="login-page">
      <div className="login-column">
        <div className="login-rule" />
        <h2 className="login-wordmark">SQL Studio</h2>
        <p className="login-purpose">
          Query, browse and manage the company's SQL Server estate from the browser.
        </p>

        <a href="/auth/login" className="login-btn">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
            <path d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92a5.06 5.06 0 01-2.2 3.32v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.1z" fill="#4285F4" />
            <path d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" fill="#34A853" />
            <path d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z" fill="#FBBC05" />
            <path d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" fill="#EA4335" />
          </svg>
          Continue with Google
        </a>

        <p className="login-note">
          Company accounts sign in directly. External collaborators need their
          address added to the guest allowlist first — ask your approver.
        </p>
      </div>
    </div>
  );
}

export default LoginPage;
