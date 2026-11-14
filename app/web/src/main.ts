import { mount } from "svelte";
import App from "./App.svelte";
import "./app.css";
import { lang } from "./lib/i18n";

document.documentElement.lang = lang;

mount(App, { target: document.getElementById("app")! });
