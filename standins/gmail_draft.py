"""STAND-IN: the mailbox client. There is no mailbox in the demo.

The Dispatch Desk reads (never sends) a dispatch mailbox to list customer
emails nobody has answered. That needs a real account and a token, neither of
which belongs in a public repository, so this stand-in simply refuses. The
desk is built to carry on without mail: the home page says the mailbox could
not be reached and every other block is unaffected.

To connect a real mailbox, replace this module with one whose get_service()
returns an authorised, read-only Gmail API service object.
"""


class NoMailbox(RuntimeError):
    pass


def get_service():
    raise NoMailbox("no mailbox in the demo")
