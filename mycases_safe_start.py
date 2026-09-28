"""Railway compatibility entrypoint.

My Cases archive/restore routes now live directly in bot.py.  Keep this
entrypoint small so Railway deployments that still use it do not register
duplicate Flask routes.
"""
import bot

if __name__ == "__main__":
    bot.main()
