from apps.core import context


class RequestContextMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
        ip = forwarded.split(",")[0].strip() if forwarded else request.META.get("REMOTE_ADDR")
        token = context.set_context(user=request.user, ip=ip or None, user_agent=request.META.get("HTTP_USER_AGENT", ""))
        try:
            return self.get_response(request)
        finally:
            context.reset_context(token)
