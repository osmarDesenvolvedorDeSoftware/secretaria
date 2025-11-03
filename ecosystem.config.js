module.exports = {
  apps: [
    {
      name: "secretaria-backend",
      script: "/root/secretaria/venv/bin/python",
      args: "-m gunicorn --bind 127.0.0.1:5005 --workers 4 run:app",
      cwd: "/root/secretaria",
      interpreter: "none",
      env: {
        FLASK_ENV: "production",
        PYTHONPATH: ".",
        PYTHONUNBUFFERED: "1",
        REDIS_URL: "redis://127.0.0.1:6379/1",
        RQ_QUEUE: "secretaria"
      },
      out_file: "/root/secretaria/logs/out.log",
      error_file: "/root/secretaria/logs/error.log",
      autorestart: true,
      max_restarts: 10,
      restart_delay: 5000
    },
    {
      name: "secretaria-worker",
      script: "/root/secretaria/venv/bin/python",
      args: "-m app.workers.rq_worker --all-tenants",
      cwd: "/root/secretaria",
      interpreter: "none",
      env: {
        FLASK_ENV: "production",
        PYTHONPATH: ".",
        PYTHONUNBUFFERED: "1",
        REDIS_URL: "redis://127.0.0.1:6379/1",
        RQ_QUEUE: "secretaria"
      },
      out_file: "/root/secretaria/logs/worker_out.log",
      error_file: "/root/secretaria/logs/worker_error.log",
      autorestart: true,
      max_restarts: 10,
      restart_delay: 5000
    }
  ]
};
