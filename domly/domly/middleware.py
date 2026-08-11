from listings.moderation_blocks import release_expired_listing_blocks
from users.moderation_blocks import release_expired_user_blocks


class ReleaseExpiredModerationBlocksMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        release_expired_user_blocks()
        release_expired_listing_blocks()
        return self.get_response(request)
