# Research Paper Concept/Structure

## 1) Titel/title

- Autoren/authors
  - Joé Welsch, Van Bao Nguyen
- Adresse/adress
  - Universität Trier, Universitätsring 15, 54296 Trier, Deutschland
  - {s4jowels,s4vonguy}@uni-trier.de

## 2) Zusammenfassung/abstract

- Paper zusammenfassung
- Abdeckung folgender Punkte mit je einem kurzen Satz
  - Motivation
  - Ziel des Papers
  - Vorgehensweise
  - Wichtige Ergebnisse
  - Fazit
- Keywords einfügen?

## 3) Introduction

- Kleine allgemeine Einführung
- Motivation
- Relevanz des Themas/Problem
- Gegebenfalls Definition (knapp halten)
- Forschungslücke
- Was wird untersucht?
- Was sind dabei die Hypothesen?
- Forschungsfrage
- Wenn ein Verfahren entwickelt werden soll, was soll das Verfahren genau leisten?
- Was soll bei der Arbeit am Ende rauskommen?
- Eine Absatz welche eine Übersicht des ganzen Papers gibt
  - Jede Section wird mit einem Satz kurz beschrieben

## 4) Grundlagen UND/ODER Related Works

- Stark eingehen auf verwandte Arbeiten
- Stand der Forschung
- RoA (einfügen urspünglicher formel von kaub)
- Gegebenenfalls Definitionen (interdependenzen, kaskadierende Effekte, Lifetimes, funktionale Zustände)
- Ableitung der Forschungslücke (Eventuell visuelle veranschaulichung)

## 5) Methodik/method: Zeitabhängiger funktionaler RoA

- Systemmodell und Annahmen
  - Systemmodell
    - Straßennetz
    - Versorgungsnetz
    - Die kritischen POIs
    - Diskrete Zeitpunkte
    - Dependencies
    - Ausfallszenario durch Hochwasser
  - Annahmen
    - Strom und Wasser binär
    - Straßenverfügbarkeit kontinuierlich durch den RoA-Pfadquotienten dargstellt
    - Überflutete Komponenten fallen aus
    - Notstrom über Lifetime
    - Repairtime
- Physische Erreichbarkeit
- Funktionale Versorgung und Lifetimes
- Funktionaler POI Score
  - Neue Formel (Multiplikationsfaktor als an und ausschalter)
- Gesamtstädtischer Index
- Topologische Kopplung
- Szenarien und Simulationsablauf
  - Level A: Nur Straßenausfälle
  - Level B: Straße und Strom
  - Leven C: Straße Strom und Wasser

## 6) Implementierung und Case Study Setup / Implementation and case study setup

*(Vielleicht in methodik einfügen?)*

- Datengrundlagen (Vielleicht Tabelle)
  - OpenStreetMap
  - Hochwasserflächen
  - OSM Power Tags
  - OSM Water Tags
  - Modellparameter
- Softwarearchitektur
- Implementierungslogik (Pseudo Code?)

## 7) Ergebnisse und Evaluation / Results and Evaluation

- Evaluationmetrics
- Vergleich der Level A, B und C
- Sensitivitätsanalyse
- Überprüfung der Hypothesen

## 8) Fazit und Ausblick

- Beantwortung der Forschungsfrage

## 9) References

- Quellen
