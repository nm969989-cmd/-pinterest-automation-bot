import time
import threading
from collections import deque
from logger import get_logger
from config import POST_DELAY_MINUTES, MAX_POSTS_PER_DAY
import datetime

logger = get_logger(__name__)

class PinScheduler:
    def __init__(self):
        self.queue = deque()
        self.is_running = False
        self.posts_today = 0
        self.last_reset_date = datetime.date.today()
        self.thread = None
        
    def add_to_queue(self, task_func, *args, **kwargs):
        """Add a pinning task to the queue"""
        self.queue.append((task_func, args, kwargs))
        logger.info(f"Added task to queue. Queue size: {len(self.queue)}")
        
    def _check_daily_reset(self):
        today = datetime.date.today()
        if today > self.last_reset_date:
            logger.info("Resetting daily post count.")
            self.posts_today = 0
            self.last_reset_date = today
            
    def _worker_loop(self):
        self.is_running = True
        logger.info(f"Scheduler started. Delay: {POST_DELAY_MINUTES}m, Max/day: {MAX_POSTS_PER_DAY}")
        
        while self.is_running:
            self._check_daily_reset()
            
            if self.queue and self.posts_today < MAX_POSTS_PER_DAY:
                # Get task
                task_func, args, kwargs = self.queue.popleft()
                
                try:
                    logger.info(f"Processing scheduled pin. Posts today: {self.posts_today+1}/{MAX_POSTS_PER_DAY}")
                    success = task_func(*args, **kwargs)
                    
                    if success:
                        self.posts_today += 1
                        logger.info(f"Pin successful. Sleeping for {POST_DELAY_MINUTES} minutes.")
                        # Sleep after successful post
                        time.sleep(POST_DELAY_MINUTES * 60)
                    else:
                        logger.error("Pin failed. Re-queueing with delay.")
                        # Add back to queue on failure, but sleep a bit to avoid spamming
                        self.queue.append((task_func, args, kwargs))
                        time.sleep(60) # 1 minute backoff
                        
                except Exception as e:
                    logger.error(f"Error executing scheduled task: {str(e)}")
                    time.sleep(60)
            else:
                if self.posts_today >= MAX_POSTS_PER_DAY:
                    logger.info("Daily limit reached. Waiting for next day...")
                    # Sleep for an hour before checking again
                    time.sleep(3600)
                else:
                    # Queue is empty, short sleep
                    time.sleep(5)
                    
    def start(self):
        if not self.is_running:
            self.thread = threading.Thread(target=self._worker_loop)
            self.thread.daemon = True
            self.thread.start()
            
    def stop(self):
        self.is_running = False
        if self.thread:
            self.thread.join(timeout=2)
            
# Global instance
scheduler = PinScheduler()
