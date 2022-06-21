import groovy.time.TimeCategory
import groovy.json.*

metadata {
    definition(
		name: "Masimo RAD-8", 
		namespace: "HeffTech", 
		author: "Troy Heffern", 
		importUrl: "https://raw.githubusercontent.com/HeffTech") {
       		capability "Initialize"

			attribute "serial_conn", "STRING"
			attribute "serial_data", "STRING"
			attribute "interface_timestamp", "DATE"
			//attribute "rad8_timestamp", "DATE"
			attribute "data_lag", "NUMBER"

			attribute "SPO2", "NUMBER"
			attribute "BPM", "NUMBER"
			attribute "PI", "NUMBER"
			//attribute "SPCO", "NUMBER"
			//attribute "SPMET", "NUMBER"
			//attribute "DESAT", "NUMBER"
			//attribute "PIDELTA", "NUMBER"
			//attribute "ALARM", "STRING"
			//attribute "EXC", "STRING"

			attribute "exc_text", "STRING"
			attribute "active_alarms", "STRING"
			attribute "alarm_history", "STRING"			
    }
}

preferences {
	input name: "host", type: "text", title: "WebSocket Host", description: "ex. IP:Port", required: true
	input name: "alarm_history_dt_override", type: "bool", title: "Override alarm history timestamp from RAD8 with interface timestamp", defaultValue: false
    input name: "alarm_history_dt_format", type: "text", title: "Alarm history date/time format", required: true, defaultValue: "h:mm a"
	input name: "calc_data_lag_enabled", type: "bool", title: "Enable data lag calculation", defaultValue: false
	input name: "websocket_connection_enabled", type: "bool", title: "Enable WebSocket connection", defaultValue: true
    input name: "debug_logging_enabled", type: "bool", title: "Enable debug logging", defaultValue: false
}

def initialize() {
    if (debug_logging_enabled) log.debug "initialize() method called"
	
	state.remove("socketStatus")

	//input checks
    if (!host) {
        log.warn "WebSocket Host not configured."
        return
    }
	
    if (!alarm_history_dt_format) {
        log.warn "Alarm history date/time format not configured."
        return
    }

	//define source_date_format
	updateDataValue("source_date_format","MM/dd/yy HH:mm:ss")
	
	//configure schedule for calculateDataLag
	if (calc_data_lag_enabled) {
		schedule("0/15 * * * * ? *", calculateDataLag)
		log.info "scheduled: calculateDataLag"
	} else {
		unschedule(calculateDataLag)
		sendEvent(name: "data_lag", value: "No Data")
		log.info "unscheduled: calculateDataLag"
	}

	//configure schedule for connectWebSocket
	if (websocket_connection_enabled) {
		schedule("0/6 * * * * ? *", connectWebSocket)
		log.info "scheduled: connectWebSocket"
		connectWebSocket()
	} else {
		unschedule(connectWebSocket)
		log.info "unscheduled: connectWebSocket"
	}
}

def updated() {
	if (debug_logging_enabled) log.debug "updated() method called"

	//close connections before applying changes
	if (state.socketStatus == "open") interfaces.webSocket.close()
	
	initialize()
}

def connectWebSocket() {
	if (debug_logging_enabled) log.debug "connectWebSocket() method called"
	
	//Connect to the Masimo RAD-8 websocket interface server if connection not already open
	if (state.socketStatus != "open") {
		try {
			interfaces.webSocket.connect("ws://${host}/")
		} 
		catch(e) {
			log.error "connection error: ${e.message}"
		}
	}
}

def webSocketStatus(String socketStatus) {
	if (debug_logging_enabled) log.debug "webSocketStatus() method called"
	
	def status = socketStatus.replace("status: ", "")
	if (state.socketStatus != status) state.socketStatus = status

	if (socketStatus.startsWith("status: open")) {
		log.info "connection opened with message: ${socketStatus}"
	} else {
		log.warn "connection not opened with message: ${socketStatus}"
		state.remove("SN")
		if (device.currentValue("serial_conn") != false) sendEvent(name: "serial_conn", value: false)
		if (device.currentValue("serial_data") != false) sendEvent(name: "serial_data", value: false)
	}
}

def parse(String message) {
	if (debug_logging_enabled) log.debug "parse() method called"	
    if (debug_logging_enabled) log.debug "message: ${message}"

	def json = null
    try{
        json = new groovy.json.JsonSlurper().parseText(message)
        if (debug_logging_enabled) log.debug "json: ${json}"
        if (json == null) {
            log.warn "message could not be parsed"
            return
        }
    }  catch(e) {
        log.error "failed to parse json e = ${e}"
        return
    }

	//log.debug json?.SN
	if (json?.SN) {
		//log.debug "true"	
		if (state.SN != json?.SN) state.SN = json?.SN
	} else {
		//log.debug "flase"	
	}
	
	if (json?.serial_data) {
		if (device.currentValue("interface_timestamp") != json?.interface_timestamp) sendEvent(name: "interface_timestamp", value: json?.interface_timestamp)
		//if (device.currentValue("rad8_timestamp") != json?.rad8_timestamp) sendEvent(name: "rad8_timestamp", value: json?.rad8_timestamp)
	} else {
		if (device.currentValue("interface_timestamp") != "No Data") sendEvent(name: "interface_timestamp", value: "No Data")
		//if (device.currentValue("rad8_timestamp") != "No Data") sendEvent(name: "rad8_timestamp", value: "No Data")
	}
	
	if (device.currentValue("SPO2") != json?.SPO2) sendEvent(name: "SPO2", value: json?.SPO2 ?: 0)
	if (device.currentValue("BPM") != json?.BPM) sendEvent(name: "BPM", value: json?.BPM ?: 0)
	if (device.currentValue("PI") != json?.PI) sendEvent(name: "PI", value: json?.PI ?: 0)
	//if (device.currentValue("SPCO") != json?.SPCO) sendEvent(name: "SPCO", value: json?.SPCO ?: 0)
	//if (device.currentValue("SPMET") != json?.SPMET) sendEvent(name: "SPMET", value: json?.SPMET ?: 0)
	//if (device.currentValue("DESAT") != json?.DESAT) sendEvent(name: "DESAT", value: json?.DESAT ?: 0)
	//if (device.currentValue("PIDELTA") != json?.PIDELTA) sendEvent(name: "PIDELTA", value: json?.PIDELTA ?: 0)
	//if (device.currentValue("ALARM") != json?.ALARM) sendEvent(name: "ALARM", value: json?.ALARM ?: "0000")
	//if (device.currentValue("EXC") != json?.EXC) sendEvent(name: "EXC", value: json?.EXC ?: "000000")

	//format exc_text
	def exc_text = ""
	json?.active_exc_list.eachWithIndex { item, i ->
		exc_text = exc_text + item
		if (i+1 != json?.active_exc_list.size()) {
			exc_text = "${exc_text},&nbsp;"
		}
	}
		
	//format active_alarms
	def active_alarms = ""
	json?.active_alarms.eachWithIndex { item, i ->
		active_alarms = active_alarms + item.alarm_text
		if (i+1 != json?.active_alarms.size()) {
			active_alarms = "${active_alarms},&nbsp;"
		}
	}
	if (active_alarms) {
		active_alarms = "<div class='blink_me'>${active_alarms}</div>"
	}

	//format alarm_history
	def alarm_history = ""
	def date_header = ""
	def date_tmp = ""
	def source_date_format = getDataValue("source_date_format")
	json?.alarm_history.eachWithIndex { item, i ->
		if (alarm_history.length() < 1000) {
			if (alarm_history_dt_override) {
				start_timestamp = item.start_interface_timestamp
			} else {
				start_timestamp = item.start_rad8_timestamp
			}

			//header
			date_tmp = Date.parse(source_date_format, start_timestamp).format("MMMM dd")
			if (date_header != date_tmp) {
				date_header = date_tmp
				alarm_history = "${alarm_history}<div style='text-align:center;'>${date_header}</div>"
			}

			//history item
			alarm_history = alarm_history + "${Date.parse(source_date_format, start_timestamp).format(alarm_history_dt_format)} ${item.alarm_text}"

			//silenced
			if (item.silenced.toInteger()) {
				alarm_history = "${alarm_history} (silenced)"
			}

			//break
			if (i+1 != json?.alarm_history.size()) {
				alarm_history = "${alarm_history}<br>"
			}
		}
	}
	if (debug_logging_enabled) log.debug "alarm_history.length:${alarm_history.length()}"

	if (device.currentValue("exc_text") != exc_text) sendEvent(name: "exc_text", value: exc_text ?: "Device Off")
	if (device.currentValue("active_alarms") != active_alarms) sendEvent(name: "active_alarms", value: active_alarms ?: "No active alarms")
	if (device.currentValue("alarm_history") != alarm_history) sendEvent(name: "alarm_history", value: alarm_history ?: "No alarm history")

	if (device.currentValue("serial_conn") != json?.serial_conn) sendEvent(name: "serial_conn", value: json?.serial_conn)
	if (device.currentValue("serial_data") != json?.serial_data) sendEvent(name: "serial_data", value: json?.serial_data)
}

def calculateDataLag() {
	if (debug_logging_enabled) log.debug "calculateDataLag() method called"	

	if (device.currentValue("serial_data") == "true") {
		try{
			def source_date_format = getDataValue("source_date_format")
			def interface_timestamp = Date.parse(source_date_format, device.currentValue("interface_timestamp"))
			def current_timestamp = new Date()

			def data_lag = (TimeCategory.minus(current_timestamp, interface_timestamp).toMilliseconds() / 1000).toDouble().round()

			if (data_lag == 1) {
				data_lag = " ${data_lag} second"
			} else {
				data_lag = "${data_lag} seconds"
			}

			if (device.currentValue("data_lag") != data_lag) sendEvent(name: "data_lag", value: data_lag)
			if (debug_logging_enabled) log.debug "data_lag: ${data_lag}"
		} catch(e) {
			if (device.currentValue("data_lag") != "No Data") sendEvent(name: "data_lag", value: "No Data")
			log.error "failed to calculate data lag e = ${e}"
			return
		}
	} else {
		if (device.currentValue("data_lag") != "No Data") sendEvent(name: "data_lag", value: "No Data")
	}
}