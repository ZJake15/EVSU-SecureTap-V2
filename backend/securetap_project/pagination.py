from rest_framework.pagination import PageNumberPagination


class StandardPagination(PageNumberPagination):
    """Lets clients opt into a larger page via ?page_size=, e.g. the dashboard's
    Users page fetching everyone in one request instead of paging through."""

    page_size_query_param = "page_size"
    max_page_size = 200
