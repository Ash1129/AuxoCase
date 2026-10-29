"""What each page shows, one module per page.

Routes in app.py stay thin: they check the request, call the page's function
here, and render its template with the dict it returns. Each function's
docstring is the contract its template relies on, so a page's data and its
markup can change together without touching the others.
"""
