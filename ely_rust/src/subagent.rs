use std::sync::{Arc, Mutex};
use crate::agent::ChatResult;
use crate::config::ProviderConfig;
use crate::tools::ToolRegistry;

pub struct BackgroundTask {
    pub id: u64,
    pub description: String,
    pub started: std::time::Instant,
    pub result: Arc<Mutex<Option<ChatResult>>>,
    pub done: Arc<std::sync::atomic::AtomicBool>,
}

impl BackgroundTask {
    pub fn spawn(id: u64, description: String, message: String,
                 config: ProviderConfig, tools: ToolRegistry,
                 system_prompt: String) -> Self {
        let result = Arc::new(Mutex::new(None));
        let done = Arc::new(std::sync::atomic::AtomicBool::new(false));
        let r = result.clone();
        let d = done.clone();

        std::thread::spawn(move || {
            let rt = tokio::runtime::Runtime::new().unwrap();
            let agent = crate::agent::Agent::new(config, tools, 5);
            let fut = agent.chat(&message, &[], &system_prompt, None);
            match rt.block_on(fut) {
                Ok(res) => { *r.lock().unwrap() = Some(res); }
                Err(e) => { *r.lock().unwrap() = Some(ChatResult {
                    reply: format!("Error: {}", e), reasoning: String::new(),
                    actions: vec![], tokens: Default::default(), model: "error".into(),
                });}
            }
            d.store(true, std::sync::atomic::Ordering::SeqCst);
        });

        Self { id, description, started: std::time::Instant::now(), result, done }
    }

    pub fn is_done(&self) -> bool {
        self.done.load(std::sync::atomic::Ordering::SeqCst)
    }

    pub fn elapsed(&self) -> f64 {
        self.started.elapsed().as_secs_f64()
    }
}

pub struct BackgroundTaskManager {
    tasks: Vec<BackgroundTask>,
    next_id: u64,
}

impl BackgroundTaskManager {
    pub fn new() -> Self { Self { tasks: Vec::new(), next_id: 1 } }

    pub fn spawn(&mut self, description: String, message: String,
                 config: ProviderConfig, tools: ToolRegistry, system_prompt: String) -> u64 {
        let id = self.next_id;
        self.next_id += 1;
        self.tasks.push(BackgroundTask::spawn(id, description, message, config, tools, system_prompt));
        id
    }

    pub fn poll(&mut self, id: u64) -> Option<String> {
        if let Some(pos) = self.tasks.iter().position(|t| t.id == id) {
            if self.tasks[pos].is_done() {
                let task = self.tasks.remove(pos);
                let result = task.result.lock().unwrap().take();
                return result.map(|r| r.reply);
            }
            return Some(format!("Task #{} still running ({:.0}s)", id, self.tasks.iter().find(|t| t.id == id).unwrap().elapsed()));
        }
        None
    }

    pub fn list(&self) -> Vec<(u64, bool, f64, String)> {
        self.tasks.iter().map(|t| (t.id, t.is_done(), t.elapsed(), t.description.clone())).collect()
    }

    pub fn kill(&mut self, id: u64) -> bool {
        if let Some(pos) = self.tasks.iter().position(|t| t.id == id) {
            self.tasks.remove(pos);
            return true;
        }
        false
    }
}
