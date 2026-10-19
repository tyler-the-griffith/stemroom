"""Expose the same shared room at / and /stemroom/ without a second worker."""
from starlette.responses import RedirectResponse


class SubpathMiddleware:
    def __init__(self, app, prefix='/stemroom'):
        self.app = app
        self.prefix = prefix

    async def __call__(self, scope, receive, send):
        path = scope.get('path', '')
        if scope['type'] in ('http', 'websocket'):
            if path == self.prefix and scope['type'] == 'http':
                query = scope.get('query_string', b'').decode('latin-1')
                target = self.prefix + '/' + ('?' + query if query else '')
                return await RedirectResponse(target, status_code=307)(scope, receive, send)
            if path.startswith(self.prefix + '/') and not scope.get('root_path'):
                scope = dict(scope, root_path=self.prefix)
        await self.app(scope, receive, send)
